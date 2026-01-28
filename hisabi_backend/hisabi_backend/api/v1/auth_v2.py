"""Auth v2 endpoints: canonical Frappe User + device-token auth (mobile-friendly).

Design goals:
- Do not rely on session cookies for mobile sync.
- Allow login by email OR normalized phone.
- Issue long-lived device tokens that are revocable per device.
- Keep backward compatibility with earlier v1 auth where possible.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Dict, Optional, Tuple

import frappe
from frappe import _
from frappe.auth import LoginManager
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import (
    issue_device_token_for_device,
    require_device_token_auth,
)
from hisabi_backend.utils.security_rate_limit import rate_limit
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_password_strength, validate_platform
from hisabi_backend.utils.wallet_acl import get_wallets_for_user
from hisabi_backend.utils.auth_lockout import on_login_failed, on_login_success, raise_if_locked
from hisabi_backend.utils.request_context import get_request_ip
from hisabi_backend.utils.audit_security import audit_security_event


PHONE_DIGITS_RE = re.compile(r"\\d+")


def _normalize_phone_e164(phone: str) -> str:
    """Normalize phone to a consistent +<digits> format.

    We keep this intentionally simple (no country inference). The uniqueness is enforced on this normalized string.
    """
    if not phone:
        frappe.throw(_("phone is required"), frappe.ValidationError)

    phone = phone.strip()
    if phone.startswith("00"):
        phone = "+" + phone[2:]

    # Keep only digits; preserve leading +.
    digits = "".join(PHONE_DIGITS_RE.findall(phone))
    if not digits:
        frappe.throw(_("Invalid phone"), frappe.ValidationError)

    if len(digits) < 8 or len(digits) > 15:
        frappe.throw(_("Invalid phone length"), frappe.ValidationError)

    return "+" + digits


def _resolve_user(identifier: str) -> str:
    if not identifier:
        frappe.throw(_("identifier is required"), frappe.ValidationError)
    identifier = identifier.strip()

    if "@" in identifier:
        user = frappe.get_value("User", {"email": identifier})
        if user:
            return user
        frappe.throw(_("User not found"), frappe.AuthenticationError)

    phone = _normalize_phone_e164(identifier)
    user = frappe.get_value("User", {"custom_phone": phone}) or frappe.get_value("User", {"mobile_no": phone})
    if user:
        return user
    frappe.throw(_("User not found"), frappe.AuthenticationError)


def _serialize_user(user: str) -> Dict[str, Any]:
    u = frappe.get_doc("User", user)
    return {
        "name": u.name,
        "full_name": u.full_name,
        "email": u.email,
        "phone": getattr(u, "custom_phone", None) or u.mobile_no,
    }


def _ensure_user_email(email: Optional[str], *, phone: Optional[str]) -> str:
    """Frappe User.name is email in most setups; for phone-only create a synthetic email."""
    if email:
        return email.strip().lower()
    if not phone:
        frappe.throw(_("email or phone is required"), frappe.ValidationError)

    # Stable synthetic email; user still logs in via phone.
    digits = "".join(PHONE_DIGITS_RE.findall(phone))
    return f"{digits}@phone.hisabi.local"


def _ensure_default_wallet_for_user(user: str, device_id: Optional[str] = None) -> str:
    """Create a default wallet and owner membership if missing."""
    # Prefer user custom default wallet if already set.
    default_wallet = frappe.get_value("User", user, "custom_default_wallet")
    if default_wallet and frappe.db.exists("Hisabi Wallet", default_wallet):
        return default_wallet

    # If user already has an active wallet membership, use the most recent one.
    row = frappe.db.get_value(
        "Hisabi Wallet Member",
        {"user": user, "status": "active"},
        ["wallet"],
        order_by="modified desc",
        as_dict=True,
    )
    if row and row.wallet:
        frappe.db.set_value("User", user, "custom_default_wallet", row.wallet, update_modified=False)
        return row.wallet

    wallet_id = f"wallet-u-{frappe.generate_hash(user, length=12)}"
    if frappe.db.exists("Hisabi Wallet", wallet_id):
        wallet_id = f"{wallet_id}-{secrets.token_hex(2)}"

    wallet = frappe.new_doc("Hisabi Wallet")
    wallet.client_id = wallet_id
    wallet.name = wallet_id
    wallet.wallet_name = "My Wallet"
    wallet.status = "active"
    wallet.owner_user = user
    wallet.created_from_device = device_id
    apply_common_sync_fields(wallet, bump_version=True, mark_deleted=False)
    wallet.save(ignore_permissions=True)

    member = frappe.new_doc("Hisabi Wallet Member")
    member.wallet = wallet_id
    member.user = user
    member.role = "owner"
    member.status = "active"
    member.joined_at = now_datetime()
    apply_common_sync_fields(member, bump_version=True, mark_deleted=False)
    member.save(ignore_permissions=True)

    frappe.db.set_value("User", user, "custom_default_wallet", wallet_id, update_modified=False)
    return wallet_id


@frappe.whitelist(allow_guest=True)
def register_user(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    full_name: Optional[str] = None,
    password: Optional[str] = None,
    device: Optional[dict] = None,
) -> Dict[str, Any]:
    """Register a user and issue a device token."""
    # Rate limit by IP (and globally per site db_name prefix).
    rate_limit(f"register:ip:{get_request_ip() or 'unknown'}", limit=3, window_seconds=600)
    ensure_roles()

    if not password:
        frappe.throw(_("password is required"), frappe.ValidationError)
    validate_password_strength(password)

    email_norm = _ensure_user_email(email, phone=phone)
    phone_norm = _normalize_phone_e164(phone) if phone else None
    if not (email_norm or phone_norm):
        frappe.throw(_("email or phone is required"), frappe.ValidationError)

    full_name = (full_name or "").strip() or "Hisabi User"

    if frappe.db.exists("User", {"email": email_norm}):
        frappe.throw(_("Account already exists"), frappe.ValidationError)
    if phone_norm and frappe.db.exists("User", {"custom_phone": phone_norm}):
        frappe.throw(_("Account already exists"), frappe.ValidationError)

    device = device or {}
    device_id = (device.get("device_id") or "").strip()
    if not device_id:
        frappe.throw(_("device.device_id is required"), frappe.ValidationError)

    platform = device.get("platform") or "web"
    platform = validate_platform(platform)
    device_name = (device.get("device_name") or "").strip() or None

    user_doc = frappe.get_doc(
        {
            "doctype": "User",
            "email": email_norm,
            "first_name": full_name,
            "enabled": 1,
            "send_welcome_email": 0,
            "user_type": "Website User",
            "roles": [{"role": "Hisabi User"}],
            "custom_phone": phone_norm,
            "custom_phone_verified": 0,
        }
    ).insert(ignore_permissions=True)
    update_password(user_doc.name, password)

    wallet_id = _ensure_default_wallet_for_user(user_doc.name, device_id=device_id)

    token, device_doc = issue_device_token_for_device(
        user=user_doc.name,
        device_id=device_id,
        platform=platform,
        device_name=device_name,
    )
    audit_security_event("login_success", user=user_doc.name, device_id=device_id, payload={"action": "register"})

    return {
        "user": _serialize_user(user_doc.name),
        "device": {"device_id": device_doc.device_id, "status": device_doc.status},
        "auth": {"token": token, "token_type": "device_token"},
        "default_wallet_id": wallet_id,
    }


@frappe.whitelist(allow_guest=True)
def login(
    identifier: str,
    password: str,
    device: Optional[dict] = None,
) -> Dict[str, Any]:
    """Login by email or phone and issue/rotate device token."""
    if not identifier:
        frappe.throw(_("identifier is required"), frappe.ValidationError)
    if not password:
        frappe.throw(_("password is required"), frappe.ValidationError)

    ip = get_request_ip() or "unknown"
    identifier_key = identifier.strip().lower()
    try:
        user = _resolve_user(identifier)
    except Exception:
        # Prevent enumeration/bruteforce for unknown identifiers.
        rate_limit(f"login:ip:{ip}:id:{identifier_key}", limit=5, window_seconds=300)
        on_login_failed(identifier, user=None, device_id=(device or {}).get("device_id"))
        raise

    raise_if_locked(user)

    # Validate password using LoginManager to respect Frappe authentication backends.
    login_manager = LoginManager()
    try:
        login_manager.authenticate(user=user, pwd=password)
    except Exception:
        rate_limit(f"login:ip:{ip}:id:{identifier_key}", limit=5, window_seconds=300)
        on_login_failed(identifier, user=user, device_id=(device or {}).get("device_id"))
        raise
    login_manager.post_login()
    on_login_success(user, device_id=(device or {}).get("device_id"))

    device = device or {}
    device_id = (device.get("device_id") or "").strip()
    if not device_id:
        frappe.throw(_("device.device_id is required"), frappe.ValidationError)
    platform = validate_platform(device.get("platform") or "web")
    device_name = (device.get("device_name") or "").strip() or None

    wallet_id = _ensure_default_wallet_for_user(user, device_id=device_id)

    token, device_doc = issue_device_token_for_device(
        user=user,
        device_id=device_id,
        platform=platform,
        device_name=device_name,
    )
    audit_security_event("token_rotated", user=user, device_id=device_id)

    return {
        "user": _serialize_user(user),
        "device": {"device_id": device_doc.device_id, "status": device_doc.status},
        "auth": {"token": token, "token_type": "device_token"},
        "default_wallet_id": wallet_id,
    }


@frappe.whitelist(allow_guest=False)
def logout() -> Dict[str, Any]:
    """Revoke the current device token (requires Authorization: Bearer token)."""
    user, device = require_device_token_auth()
    device.status = "revoked"
    device.token_hash = None
    device.device_token_hash = None
    device.expires_at = None
    device.save(ignore_permissions=True)
    audit_security_event("device_revoked", user=user, device_id=device.device_id, payload={"action": "logout"})
    return {"status": "revoked", "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def me() -> Dict[str, Any]:
    """Return current user profile and wallet memberships (requires device token)."""
    user, device = require_device_token_auth()
    return {
        "user": _serialize_user(user),
        "device": {"device_id": device.device_id, "status": device.status, "last_seen_at": device.last_seen_at},
        "wallets": get_wallets_for_user(user),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def device_revoke(device_id: str) -> Dict[str, Any]:
    """Revoke a specific device for the current user (requires device token)."""
    user, _ = require_device_token_auth()
    rate_limit(f"device_revoke:user:{user}", limit=5, window_seconds=60)
    device_id = (device_id or "").strip()
    if not device_id:
        frappe.throw(_("device_id is required"), frappe.ValidationError)

    name = frappe.get_value("Hisabi Device", {"device_id": device_id})
    if not name:
        frappe.throw(_("Device not found"), frappe.ValidationError)
    device = frappe.get_doc("Hisabi Device", name)
    if device.user != user and "System Manager" not in frappe.get_roles(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    device.status = "revoked"
    device.token_hash = None
    device.device_token_hash = None
    device.expires_at = None
    device.save(ignore_permissions=True)
    audit_security_event("device_revoked", user=user, device_id=device.device_id)
    return {"status": "revoked", "server_time": now_datetime().isoformat()}
