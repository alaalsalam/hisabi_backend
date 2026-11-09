"""User-scoped notification inbox emit helper.

Notifications are addressed to a specific recipient user (NOT wallet-scoped), so they can reach a
person who is not a member of the sender's wallet. Emission is strictly best-effort: a notification
failure must never fail the action that triggered it. The doctype is created by a pipeline migration,
so everything stays a no-op (guarded by table_exists) until the user runs Approve & continue.
"""

from __future__ import annotations

import json
from typing import Optional

import frappe

NOTIFICATION_EVENT = "hisabi_user_notification_event"


def create_user_notification(
    user: str,
    notif_type: str,
    title: str,
    body: str = "",
    *,
    action_route: Optional[str] = None,
    ref_doctype: Optional[str] = None,
    ref_name: Optional[str] = None,
    from_user: Optional[str] = None,
    from_name: Optional[str] = None,
) -> Optional[str]:
    """Create an inbox notification for `user` and nudge them over realtime. Best-effort, never raises."""
    if not user or user == "Guest":
        return None
    try:
        if not frappe.db.table_exists("Hisabi Notification"):
            return None

        doc = frappe.new_doc("Hisabi Notification")
        doc.user = user
        doc.notif_type = notif_type
        doc.title = title
        doc.body = body or ""
        doc.action_route = action_route
        doc.ref_doctype = ref_doctype
        doc.ref_name = ref_name
        doc.from_user = from_user
        doc.from_name = from_name
        doc.is_read = 0
        doc.is_deleted = 0
        doc.insert(ignore_permissions=True)

        # Instant delivery when the recipient's socket is connected; the client's poll/on-open is the
        # fallback when it is not.
        frappe.publish_realtime(
            event=NOTIFICATION_EVENT, message={"type": notif_type}, user=user, after_commit=True
        )
        return doc.name
    except Exception as e:
        frappe.log_error(
            "error in notifications: create_user_notification",
            json.dumps(
                {
                    "user": frappe.session.user,
                    "recipient": user,
                    "notif_type": notif_type,
                    "datetime": frappe.utils.now(),
                    "error": str(e),
                },
                default=str,
            ),
        )
        return None
