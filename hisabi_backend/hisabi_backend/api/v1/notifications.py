"""User notification inbox APIs (v1).

Every endpoint scopes strictly to the authenticated user — the recipient filter is never taken from
the client. The doctype is migrated by the pipeline, so all endpoints degrade to empty/no-op (guarded
by table_exists) until then instead of raising.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import now_datetime

from hisabi_backend.utils.security import require_device_token_auth


def _table_ready() -> bool:
    return bool(frappe.db.table_exists("Hisabi Notification"))


@frappe.whitelist(allow_guest=False)
def notifications_list(device_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """Unread, undeleted notifications for the current user (returning only unread avoids client-side
    resurrection: once marked read on the server it never comes back)."""
    user, _device = require_device_token_auth()
    if not _table_ready():
        return {"notifications": []}

    try:
        capped = min(max(int(limit), 1), 100)
    except (TypeError, ValueError):
        capped = 50

    rows = frappe.get_all(
        "Hisabi Notification",
        filters={"user": user, "is_read": 0, "is_deleted": 0},
        fields=[
            "name",
            "notif_type",
            "title",
            "body",
            "action_route",
            "from_name",
            "ref_doctype",
            "ref_name",
            "creation",
        ],
        order_by="creation desc",
        limit=capped,
    )
    return {
        "notifications": [
            {
                "id": r.get("name"),
                "type": r.get("notif_type"),
                "title": r.get("title"),
                "body": r.get("body"),
                "action_route": r.get("action_route"),
                "from_name": r.get("from_name"),
                "ref_doctype": r.get("ref_doctype"),
                "ref_name": r.get("ref_name"),
                "created": str(r.get("creation")) if r.get("creation") else None,
            }
            for r in rows
        ]
    }


def _owned_names(user: str, ids: List[str]) -> List[str]:
    ids = [str(i) for i in ids if i]
    if not ids:
        return []
    return frappe.get_all(
        "Hisabi Notification",
        filters={"name": ["in", ids], "user": user},
        pluck="name",
    )


def _coerce_ids(ids: Any) -> List[str]:
    if isinstance(ids, str):
        stripped = ids.strip()
        if stripped.startswith("["):
            parsed = frappe.parse_json(stripped)
            return [str(i) for i in parsed] if isinstance(parsed, list) else []
        return [stripped] if stripped else []
    if isinstance(ids, list):
        return [str(i) for i in ids]
    return []


@frappe.whitelist(allow_guest=False)
def notifications_mark_read(ids: Any = None, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    if not _table_ready():
        return {"status": "ok", "updated": 0}
    owned = _owned_names(user, _coerce_ids(ids))
    if owned:
        frappe.db.set_value(
            "Hisabi Notification", {"name": ["in", owned]}, {"is_read": 1, "read_at": now_datetime()}, update_modified=False
        )
    return {"status": "ok", "updated": len(owned)}


@frappe.whitelist(allow_guest=False)
def notifications_mark_all_read(device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    if not _table_ready():
        return {"status": "ok", "updated": 0}
    unread_filters = {"user": user, "is_read": 0, "is_deleted": 0}
    count = frappe.db.count("Hisabi Notification", unread_filters)
    if count:
        frappe.db.set_value(
            "Hisabi Notification", unread_filters, {"is_read": 1, "read_at": now_datetime()}, update_modified=False
        )
    return {"status": "ok", "updated": count}


@frappe.whitelist(allow_guest=False)
def notification_dismiss(notification_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    if not _table_ready():
        return {"status": "ok", "updated": 0}
    owned = _owned_names(user, [notification_id])
    if owned:
        frappe.db.set_value(
            "Hisabi Notification", {"name": ["in", owned]}, {"is_deleted": 1, "is_read": 1}, update_modified=False
        )
    return {"status": "ok", "updated": len(owned)}
