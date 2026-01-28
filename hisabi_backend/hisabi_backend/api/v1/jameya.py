"""Jameya APIs (v1)."""

from __future__ import annotations

from typing import Optional

import frappe
from frappe import _
from frappe.utils import now_datetime

from hisabi_backend.domain.recalc_engine import recalc_jameya_status
from hisabi_backend.utils.security import require_device_token_auth


@frappe.whitelist(allow_guest=False)
def rebuild_schedule(jameya_id: str, device_id: Optional[str] = None):
    user, _device = require_device_token_auth()
    jameya = frappe.get_doc("Hisabi Jameya", jameya_id)
    if jameya.user != user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    frappe.db.delete("Hisabi Jameya Payment", {"jameya": jameya_id})
    jameya._ensure_schedule()
    recalc_jameya_status(user, jameya_id)

    return {"status": "ok", "jameya": jameya_id}


@frappe.whitelist(allow_guest=False)
def mark_payment_paid(payment_id: str, tx_id: Optional[str] = None, device_id: Optional[str] = None):
    user, _device = require_device_token_auth()
    payment = frappe.get_doc("Hisabi Jameya Payment", payment_id)
    if payment.user != user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    payment.paid_at = now_datetime()
    payment.status = "received" if payment.is_my_turn else "paid"
    payment.save(ignore_permissions=True)

    recalc_jameya_status(user, payment.jameya)
    return {"status": "ok", "payment": payment_id, "tx_id": tx_id}
