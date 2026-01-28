"""Device management endpoints."""

import frappe
from frappe import _
from frappe.utils import now_datetime

from hisabi_backend.utils.audit_security import audit_security_event
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields


@frappe.whitelist(allow_guest=False)
def revoke_device(device_id: str) -> dict:
    """(Legacy) Revoke a device for the current user. Requires device token auth."""
    if not device_id:
        frappe.throw(_("device_id is required"), frappe.ValidationError)

    user, current = require_device_token_auth()

    filters = {"device_id": device_id}
    if not (frappe.has_role("Hisabi Admin") or frappe.has_role("System Manager")):
        filters["user"] = user

    device_name = frappe.get_value("Hisabi Device", filters)
    if not device_name:
        frappe.throw(_("Device not found"), frappe.DoesNotExistError)

    device = frappe.get_doc("Hisabi Device", device_name)
    device.status = "revoked"
    now_ms = int(now_datetime().timestamp() * 1000)
    device.updated_at_ms = now_ms
    apply_common_sync_fields(device, {"client_modified_ms": now_ms}, bump_version=True, mark_deleted=False)
    device.save(ignore_permissions=True)

    device.device_token_hash = None
    device.token_hash = None
    device.save(ignore_permissions=True)
    audit_security_event("device_revoked", user=user, device_id=device.device_id)

    return {"status": "revoked"}


@frappe.whitelist(allow_guest=False)
def devices_list() -> dict:
    """List devices for the current user (device token auth)."""
    user, current = require_device_token_auth()
    rows = frappe.get_all(
        "Hisabi Device",
        filters={"user": user, "is_deleted": 0},
        fields=[
            "device_id",
            "device_name",
            "platform",
            "status",
            "last_seen_at",
        ],
        order_by="last_seen_at desc, modified desc",
    )
    for r in rows:
        r["is_current"] = bool(r.get("device_id") == current.device_id)

    return {"devices": rows, "server_time": now_datetime().isoformat()}
