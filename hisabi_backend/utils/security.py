"""Security helpers for authentication and authorization."""

from __future__ import annotations

import base64
import hashlib
import secrets

import frappe
from frappe.utils.password import get_decrypted_password, passlibctx, set_encrypted_password

from hisabi_backend.utils.request_context import get_request_ip, get_user_agent
from hisabi_backend.utils.audit_security import audit_security_event


def get_or_create_api_credentials(user: str) -> dict:
    """Return API key/secret for a user, creating them if missing."""
    user_doc = frappe.get_doc("User", user)

    api_key = user_doc.api_key
    if not api_key:
        api_key = frappe.generate_hash(length=20)
        user_doc.api_key = api_key

    api_secret = get_decrypted_password("User", user, "api_secret", raise_exception=False)
    if not api_secret:
        api_secret = frappe.generate_hash(length=40)
        set_encrypted_password("User", user, api_secret, "api_secret")

    if user_doc.is_dirty():
        user_doc.save(ignore_permissions=True)

    return {
        "type": "api_key",
        "api_key": api_key,
        "api_secret": api_secret,
    }


def generate_device_token() -> str:
    """Generate a long random device token."""
    return frappe.generate_hash(length=64)


def hash_device_token(token: str) -> str:
    """Hash a device token using passlib."""
    return passlibctx.hash(token)


def verify_device_token(token_hash: str, token: str) -> bool:
    """Verify a device token against its stored hash."""
    if not token_hash or not token:
        return False
    return passlibctx.verify(token, token_hash)


def get_bearer_token() -> str | None:
    header = frappe.get_request_header("Authorization")
    if not header and getattr(frappe.local, "request", None):
        header = frappe.local.request.headers.get("Authorization")
    if not header:
        return None
    if not header.lower().startswith("bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def _get_token_salt() -> str:
    # Prefer a dedicated salt; fallback to Frappe's encryption_key.
    salt = frappe.local.conf.get("hisabi_token_salt") or frappe.local.conf.get("encryption_key")
    if not salt:
        frappe.throw("Missing server token salt (set encryption_key in site_config.json)", frappe.ValidationError)
    return str(salt)


def generate_device_token_v2() -> str:
    """Generate a revocable long-lived device token (raw)."""
    raw = secrets.token_bytes(32)
    return "hisabi_" + base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def hash_device_token_v2(token: str) -> str:
    """Hash token as sha256(token + server_salt). Stored hash is safe to persist."""
    salt = _get_token_salt()
    digest = hashlib.sha256((token + salt).encode("utf-8")).hexdigest()
    return digest


def verify_device_token_v2(token_hash: str, token: str) -> bool:
    if not token_hash or not token:
        return False
    return secrets.compare_digest(token_hash, hash_device_token_v2(token))


def issue_device_token_for_device(
    *, user: str, device_id: str, platform: str, device_name: str | None = None
) -> tuple[str, frappe.model.document.Document]:
    """Upsert device and rotate token. Returns (raw_token, device_doc)."""
    if not user or user == "Guest":
        frappe.throw("Authentication required", frappe.AuthenticationError)
    device_id = (device_id or "").strip()
    if not device_id:
        frappe.throw("device_id is required", frappe.ValidationError)

    name = frappe.get_value("Hisabi Device", {"device_id": device_id})
    if name:
        device = frappe.get_doc("Hisabi Device", name)
        if device.user != user and device.status != "revoked":
            frappe.throw("Device already linked to another user", frappe.PermissionError)
    else:
        device = frappe.new_doc("Hisabi Device")
        device.device_id = device_id
        device.client_id = device_id
        device.name = device_id

    device.user = user
    device.platform = platform
    if device_name:
        device.device_name = device_name
    device.status = "active"
    device.last_seen_at = frappe.utils.now_datetime()

    token = generate_device_token_v2()
    device.token_hash = hash_device_token_v2(token)
    device.token_last4 = token[-4:]
    device.issued_at = frappe.utils.now_datetime()
    device.expires_at = None

    # Backward-compat: also set old field if present.
    if hasattr(device, "device_token_hash"):
        device.device_token_hash = hash_device_token(token)

    device.save(ignore_permissions=True)
    return token, device


def require_device_token_auth(*, expected_device_id: str | None = None) -> tuple[str, frappe.model.document.Document]:
    """Authenticate request using Authorization: Bearer <device_token>.

    If expected_device_id is provided, it must match the token's device record.
    """
    token = get_bearer_token()
    if not token:
        frappe.throw("Authorization bearer token required", frappe.AuthenticationError)

    if not token.startswith("hisabi_"):
        frappe.throw("Invalid device token", frappe.AuthenticationError)

    token_hash = hash_device_token_v2(token)
    name = frappe.get_value("Hisabi Device", {"token_hash": token_hash})
    if not name:
        frappe.throw("Invalid device token", frappe.AuthenticationError)
    device = frappe.get_doc("Hisabi Device", name)

    if expected_device_id and device.device_id != expected_device_id:
        frappe.throw("device_id does not match token", frappe.AuthenticationError)

    if device.status == "revoked":
        audit_security_event("token_revoked", user=device.user, device_id=device.device_id, payload={"reason": "revoked"})
        frappe.throw("token_revoked", frappe.AuthenticationError)

    if getattr(device, "expires_at", None) and device.expires_at < frappe.utils.now_datetime():
        audit_security_event("token_expired", user=device.user, device_id=device.device_id, payload={"reason": "expired"})
        frappe.throw("token_expired", frappe.AuthenticationError)

    device.last_seen_at = frappe.utils.now_datetime()
    if hasattr(device, "last_seen_ip"):
        device.last_seen_ip = get_request_ip()
    if hasattr(device, "last_seen_user_agent"):
        device.last_seen_user_agent = get_user_agent()
    device.save(ignore_permissions=True)

    user = device.user
    if not user:
        frappe.throw("Device user not found", frappe.AuthenticationError)

    frappe.set_user(user)
    return user, device


def require_device_auth(device_id: str) -> tuple[str, frappe.model.document.Document]:
    # Prefer v2 token auth; fall back to legacy passlib hash on the device record.
    try:
        return require_device_token_auth(expected_device_id=device_id)
    except frappe.AuthenticationError:
        token = get_bearer_token()
        if not token:
            raise
        device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
        if not device_name:
            raise
        device = frappe.get_doc("Hisabi Device", device_name)
        if device.status == "revoked":
            frappe.throw("Device revoked", frappe.PermissionError)
        if not verify_device_token(getattr(device, "device_token_hash", ""), token):
            raise
        user = device.user
        frappe.set_user(user)
        return user, device


def require_user_or_device(device_id: str | None = None) -> tuple[str, frappe.model.document.Document | None]:
    # Legacy helper: prefer device tokens; session cookies are not suitable for mobile sync.
    token = get_bearer_token()
    if token:
        if device_id:
            user, device = require_device_auth(device_id)
            return user, device
        user, device = require_device_token_auth()
        return user, device

    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication required", frappe.AuthenticationError)
    return user, None
