"""Auth v2 endpoints: canonical Frappe User + device-token auth (mobile-friendly).

Design goals:
- Do not rely on session cookies for mobile sync.
- Allow login by email OR normalized phone.
- Issue long-lived device tokens that are revocable per device.
- Keep backward compatibility with earlier v1 auth where possible.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe import _
from frappe.auth import LoginManager
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import (
    ensure_device_for_user,
    issue_device_token_for_device,
    require_device_token_auth,
)
from hisabi_backend.utils.api_errors import error_response
from hisabi_backend.utils.security_rate_limit import rate_limit
from hisabi_backend.utils.validators import normalize_and_validate_phone, validate_password_strength, validate_platform
from hisabi_backend.utils.wallet_acl import (
    ensure_default_wallet_for_user,
    get_or_create_hisabi_user,
    get_wallets_for_user,
)
from hisabi_backend.utils.auth_lockout import on_login_failed, on_login_success, raise_if_locked
from hisabi_backend.utils.request_context import get_request_ip
from hisabi_backend.utils.audit_security import audit_security_event
from hisabi_backend.utils.request_headers import strip_expect_header


def _resolve_user(identifier: str) -> str:
    if not identifier:
        frappe.throw(_("identifier is required"), frappe.ValidationError)
    identifier = identifier.strip()

    if "@" in identifier:
        user = frappe.get_value("User", {"email": identifier.lower()})
        if user:
            return user
        frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)

    phone_norm = normalize_and_validate_phone(identifier)
    phone_digits = phone_norm[1:] if phone_norm.startswith("+") else phone_norm
    phone_variants = tuple(dict.fromkeys((phone_digits, f"+{phone_digits}")))
    user = None
    for variant in phone_variants:
        user = frappe.get_value("User", {"phone": variant}) or frappe.get_value("User", {"mobile_no": variant})
        if user:
            break
    if user:
        return user
    frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)


def _serialize_user(user: str) -> Dict[str, Any]:
    u = frappe.get_doc("User", user)
    return {
        "name": u.name,
        "full_name": u.full_name,
        "email": u.email,
        "phone": getattr(u, "phone", None) or getattr(u, "mobile_no", None) or getattr(u, "phone", None),
    }


def _ensure_user_email(email: Optional[str], *, phone_digits: Optional[str]) -> str:
    """Frappe User.name is email in most setups; for phone-only create a synthetic email."""
    if email:
        email_norm = email.strip().lower()
        if email_norm:
            return email_norm
    if not phone_digits:
        frappe.throw(_("email or phone is required"), frappe.ValidationError)

    # Stable synthetic email; user still logs in via phone.
    return f"{phone_digits}@phone.hisabi.local"




@frappe.whitelist(allow_guest=True)
def register_user(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    full_name: Optional[str] = None,
    password: Optional[str] = None,
    device: Optional[dict] = None,
) -> Dict[str, Any]:
    """Register a user and issue a device token."""
    try:
        # Rate limit by IP (and globally per site db_name prefix).
        # Keep abuse guard while avoiding false lockouts during normal QA / onboarding retries.
        rate_limit(f"register:ip:{get_request_ip() or 'unknown'}", limit=20, window_seconds=600)
        ensure_roles()

        if not password:
            frappe.throw(_("password is required"), frappe.ValidationError)
        validate_password_strength(password)

        phone_norm = normalize_and_validate_phone(phone) if phone else None
        phone_digits = phone_norm[1:] if (phone_norm and phone_norm.startswith("+")) else phone_norm
        email_norm = _ensure_user_email(email, phone_digits=phone_digits)
        if not (email_norm or phone_digits):
            frappe.throw(_("email or phone is required"), frappe.ValidationError)

        full_name = (full_name or "").strip() or "Hisabi User"

        if frappe.db.exists("User", {"email": email_norm}):
            frappe.throw(_("Account already exists"), frappe.ValidationError)
        if phone_digits:
            phone_variants = tuple(dict.fromkeys((phone_digits, f"+{phone_digits}")))
            if frappe.db.exists("User", {"phone": ["in", list(phone_variants)]}) or frappe.db.exists(
                "User", {"mobile_no": ["in", list(phone_variants)]}
            ):
                frappe.throw(_("Account already exists"), frappe.ValidationError)

        device = device or {}
        device_id = (device.get("device_id") or "").strip()
        if not device_id:
            frappe.throw(_("device.device_id is required"), frappe.ValidationError)

        platform = device.get("platform") or "web"
        platform = validate_platform(platform)
        device_name = (device.get("device_name") or "").strip() or None

        _device, error = ensure_device_for_user(user=email_norm, device_id=device_id)
        if error:
            return error

        user_doc = frappe.get_doc(
            {
                "doctype": "User",
                "email": email_norm,
                "first_name": full_name,
                "enabled": 1,
                "send_welcome_email": 0,
                "user_type": "Website User",
                "roles": [{"role": "Hisabi User"}],
                # Store canonical digits-only form to keep lookups stable across +prefix input styles.
                "phone": phone_digits,
                "mobile_no": phone_digits,
            }
        ).insert(ignore_permissions=True)
        update_password(user_doc.name, password)

        get_or_create_hisabi_user(user_doc.name)

        wallet_id = ensure_default_wallet_for_user(user_doc.name, device_id=device_id)

        token, device_doc = issue_device_token_for_device(
            user=user_doc.name,
            device_id=device_id,
            platform=platform,
            device_name=device_name,
            wallet_id=wallet_id,
        )
        audit_security_event("login_success", user=user_doc.name, device_id=device_id, payload={"action": "register"})

        frappe.clear_messages()
        return {
            "user": _serialize_user(user_doc.name),
            "device": {"device_id": device_doc.device_id, "status": device_doc.status},
            "auth": {"token": token, "token_type": "device_token"},
            "default_wallet_id": wallet_id,
        }
    except frappe.ValidationError as exc:
        message = str(exc)
        lowered = message.lower()
        if "rate_limited" in lowered:
            return error_response(
                status_code=429,
                code="RATE_LIMITED",
                message="rate_limited",
                user_message="محاولات كثيرة خلال فترة قصيرة. انتظر قليلًا ثم حاول مرة أخرى.",
                action="retry",
            )
        if "account already exists" in lowered:
            return error_response(
                status_code=409,
                code="ACCOUNT_EXISTS",
                message=message,
                user_message="هذا الحساب موجود بالفعل. استخدم تسجيل الدخول بدل إنشاء حساب جديد.",
                action="retry",
            )
        return error_response(
            status_code=400,
            code="VALIDATION_ERROR",
            message=message,
            user_message="بيانات غير صحيحة. يرجى التحقق من الهاتف وكلمة المرور ثم المحاولة مرة أخرى.",
            action="retry",
        )
    except frappe.AuthenticationError as exc:
        return error_response(
            status_code=401,
            code="UNAUTHORIZED",
            message=str(exc),
            user_message="تعذر تسجيل الدخول. يرجى التحقق من البيانات.",
            action="retry",
        )
    except frappe.PermissionError as exc:
        return error_response(
            status_code=403,
            code="FORBIDDEN",
            message=str(exc),
            user_message="غير مصرح.",
            action="contact_support",
        )


@frappe.whitelist(allow_guest=True)
def login(
    identifier: str,
    password: str,
    device: Optional[dict] = None,
) -> Dict[str, Any]:
    """Login by email or phone and issue/rotate device token."""
    strip_expect_header()
    try:
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
            frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)
        login_manager.post_login()
        on_login_success(user, device_id=(device or {}).get("device_id"))

        device = device or {}
        device_id = (device.get("device_id") or "").strip()
        if not device_id:
            frappe.throw(_("device.device_id is required"), frappe.ValidationError)
        platform = validate_platform(device.get("platform") or "web")
        device_name = (device.get("device_name") or "").strip() or None

        _device, error = ensure_device_for_user(user=user, device_id=device_id)
        if error:
            return error

        wallet_id = ensure_default_wallet_for_user(user, device_id=device_id)

        token, device_doc = issue_device_token_for_device(
            user=user,
            device_id=device_id,
            platform=platform,
            device_name=device_name,
            wallet_id=wallet_id,
        )
        audit_security_event("token_rotated", user=user, device_id=device_id)

        frappe.clear_messages()
        return {
            "user": _serialize_user(user),
            "device": {"device_id": device_doc.device_id, "status": device_doc.status},
            "auth": {"token": token, "token_type": "device_token"},
            "default_wallet_id": wallet_id,
        }
    except frappe.ValidationError as exc:
        message = str(exc)
        if "rate_limited" in message.lower():
            return error_response(
                status_code=429,
                code="RATE_LIMITED",
                message="rate_limited",
                user_message="عدد محاولات تسجيل الدخول كبير جدًا. انتظر قليلًا ثم حاول مرة أخرى.",
                action="retry",
            )
        return error_response(
            status_code=400,
            code="VALIDATION_ERROR",
            message=message,
            user_message="بيانات غير صحيحة. يرجى التحقق والمحاولة مرة أخرى.",
            action="retry",
        )
    except frappe.AuthenticationError as exc:
        return error_response(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message=str(exc),
            user_message="بيانات الدخول غير صحيحة.",
            action="retry",
        )
    except frappe.PermissionError as exc:
        return error_response(
            status_code=403,
            code="FORBIDDEN",
            message=str(exc),
            user_message="غير مصرح.",
            action="contact_support",
        )


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
    strip_expect_header()
    try:
        user, device = require_device_token_auth()
        default_wallet_id = ensure_default_wallet_for_user(user, device_id=device.device_id)
        return {
            "user": _serialize_user(user),
            "device": {"device_id": device.device_id, "status": device.status, "last_seen_at": device.last_seen_at},
            "default_wallet_id": default_wallet_id,
            "wallets": get_wallets_for_user(user, default_wallet_id=default_wallet_id),
            "server_time": now_datetime().isoformat(),
        }
    except frappe.ValidationError as exc:
        return error_response(
            status_code=400,
            code="VALIDATION_ERROR",
            message=str(exc),
            user_message="طلب غير صالح.",
            action="retry",
        )
    except frappe.AuthenticationError as exc:
        return error_response(
            status_code=401,
            code="UNAUTHORIZED",
            message=str(exc),
            user_message="تحتاج إلى تسجيل الدخول.",
            action="retry",
        )
    except frappe.PermissionError as exc:
        return error_response(
            status_code=403,
            code="FORBIDDEN",
            message=str(exc),
            user_message="غير مصرح.",
            action="contact_support",
        )


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
