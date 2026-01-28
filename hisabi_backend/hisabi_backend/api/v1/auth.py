"""Authentication endpoints for the mobile client."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.auth import LoginManager
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import generate_device_token, hash_device_token
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import normalize_phone, validate_password_strength, validate_platform


def _get_or_create_device(user: str, device_id: str):
    name = frappe.get_value("Hisabi Device", {"user": user, "device_id": device_id})
    if name:
        return frappe.get_doc("Hisabi Device", name)

    return frappe.new_doc("Hisabi Device")


def _resolve_user_by_identifier(identifier: str) -> str:
    if not identifier:
        frappe.throw(_("login is required"), frappe.ValidationError)

    identifier = identifier.strip()
    if "@" in identifier:
        user = frappe.get_value("User", {"email": identifier})
        if user:
            return user
    phone = normalize_phone(identifier)
    user = frappe.get_value("User", {"mobile_no": phone}) or frappe.get_value("User", {"phone": phone})
    if user:
        return user

    frappe.throw(_("User not found"), frappe.AuthenticationError)


def _serialize_user_profile(user: str) -> dict:
    user_doc = frappe.get_doc("User", user)
    return {
        "name": user_doc.name,
        "email": user_doc.email,
        "full_name": user_doc.full_name,
        "mobile_no": user_doc.mobile_no,
        "created_at": user_doc.creation,
    }


@frappe.whitelist(allow_guest=True)
def login(login: str | None = None, password: str | None = None, identifier: str | None = None) -> dict:
    """Login via email or phone."""
    login_value = login or identifier
    if not login_value:
        frappe.throw(_("login is required"), frappe.ValidationError)
    if not password:
        frappe.throw(_("password is required"), frappe.ValidationError)

    user = _resolve_user_by_identifier(login_value)

    login_manager = LoginManager()
    login_manager.authenticate(user=user, pwd=password)
    login_manager.post_login()

    frappe.set_user(user)
    return {
        "user": user,
        "profile": _serialize_user_profile(user),
    }


@frappe.whitelist(allow_guest=True)
def register(
    email: str | None = None,
    phone: str | None = None,
    password: str | None = None,
    full_name: str | None = None,
) -> dict:
    """Register a new user with email or phone."""
    ensure_roles()

    if not password:
        frappe.throw(_("password is required"), frappe.ValidationError)
    validate_password_strength(password)

    email = (email or "").strip()
    phone = normalize_phone(phone) if phone else ""
    if not email and not phone:
        frappe.throw(_("email or phone is required"), frappe.ValidationError)

    if email and frappe.db.exists("User", {"email": email}):
        frappe.throw(_("Email already registered"), frappe.ValidationError)

    if phone and frappe.db.exists("User", {"mobile_no": phone}):
        frappe.throw(_("Phone already registered"), frappe.ValidationError)

    if not email:
        email = f"{phone}@hisabi.local"

    full_name = (full_name or "Hisabi User").strip()

    user_doc = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": full_name,
        "last_name": "",
        "mobile_no": phone or None,
        "send_welcome_email": 0,
        "enabled": 1,
        "roles": [{"role": "Hisabi User"}],
    }).insert(ignore_permissions=True)

    update_password(user_doc.name, password)

    return {
        "user": user_doc.name,
        "profile": _serialize_user_profile(user_doc.name),
    }


@frappe.whitelist(allow_guest=False)
def register_device(
    device_id: str,
    platform: str,
    device_name: str | None = None,
    client_id: str | None = None,
) -> dict:
    """Register or update a device for the current user."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    device_id = (device_id or "").strip()
    if not device_id:
        frappe.throw(_("device_id is required"), frappe.ValidationError)

    platform = validate_platform(platform)

    existing_device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
    existing = None
    if existing_device_name:
        existing = frappe.get_doc("Hisabi Device", existing_device_name)
        if existing.user != user and existing.status != "revoked":
            frappe.throw(_("Device already linked to another user"), frappe.PermissionError)

    device = existing or _get_or_create_device(user, device_id)
    device.user = user
    device.device_id = device_id
    device.client_id = client_id or device_id
    if device.is_new():
        device.name = device.client_id
    device.platform = platform
    if device_name:
        device.device_name = device_name
    device.status = "active"
    device.client_id = device.client_id or device_id

    now_ms = int(now_datetime().timestamp() * 1000)
    if not device.created_at_ms:
        device.created_at_ms = now_ms
    device.updated_at_ms = now_ms

    apply_common_sync_fields(device, {"client_modified_ms": now_ms}, bump_version=True, mark_deleted=False)

    device_token = generate_device_token()
    device.device_token_hash = hash_device_token(device_token)

    device.save(ignore_permissions=True)

    return {
        "device_id": device.device_id,
        "device_token": device_token,
        "status": device.status,
    }


@frappe.whitelist(allow_guest=False)
def link_device_to_user(device_id: str, platform: str | None = None) -> dict:
    """Link a device to the current user after login."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    device_id = (device_id or "").strip()
    if not device_id:
        frappe.throw(_("device_id is required"), frappe.ValidationError)

    existing_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
    if existing_name:
        device = frappe.get_doc("Hisabi Device", existing_name)
        if device.user != user and device.status != "revoked":
            frappe.throw(_("Device already linked to another user"), frappe.PermissionError)
    else:
        device = frappe.new_doc("Hisabi Device")

    device.user = user
    device.device_id = device_id
    device.client_id = device_id
    if device.is_new():
        device.name = device_id
    if platform:
        device.platform = validate_platform(platform)
    elif not device.platform:
        device.platform = "web"
    device.status = "active"

    now_ms = int(now_datetime().timestamp() * 1000)
    if not device.created_at_ms:
        device.created_at_ms = now_ms
    device.updated_at_ms = now_ms

    apply_common_sync_fields(device, {"client_modified_ms": now_ms}, bump_version=True, mark_deleted=False)

    device_token = generate_device_token()
    device.device_token_hash = hash_device_token(device_token)
    device.save(ignore_permissions=True)

    return {
        "device_id": device.device_id,
        "device_token": device_token,
        "status": device.status,
    }
