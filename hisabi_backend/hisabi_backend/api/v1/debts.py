"""Debt network APIs (v1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe import _

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.validators import normalize_phone


@frappe.whitelist(allow_guest=False)
def create_network_request(
    to_phone: str,
    payload: Dict[str, Any],
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    to_phone = normalize_phone(to_phone)

    from_phone = frappe.get_value("User", user, "phone") or user

    request = frappe.new_doc("Hisabi Debt Request")
    request.user = user
    request.client_id = payload.get("client_id") or frappe.generate_hash(length=12)
    request.name = request.client_id
    request.from_phone = from_phone
    request.to_phone = to_phone
    request.debt_payload_json = payload
    request.status = "pending"
    request.insert(ignore_permissions=True)

    return {"status": "ok", "request_id": request.name}


@frappe.whitelist(allow_guest=False)
def accept_request(request_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    request = frappe.get_doc("Hisabi Debt Request", request_id)

    if request.is_deleted:
        frappe.throw(_("Request is deleted"), frappe.ValidationError)

    user_phone = frappe.get_value("User", user, "phone")
    if request.user != user and user_phone and normalize_phone(user_phone) != normalize_phone(request.to_phone or ""):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    request.status = "accepted"
    request.save(ignore_permissions=True)

    payload = request.debt_payload_json or request.debt_payload or {}
    if not payload:
        return {"status": "accepted", "debt": None}

    debt = frappe.new_doc("Hisabi Debt")
    debt.user = user
    debt.client_id = payload.get("client_id") or frappe.generate_hash(length=12)
    debt.name = debt.client_id
    debt.debt_name = payload.get("debt_name") or payload.get("counterparty_name") or "Debt"
    debt.direction = payload.get("direction") or "owe"
    debt.currency = payload.get("currency")
    debt.principal_amount = payload.get("principal_amount") or payload.get("amount")
    debt.remaining_amount = debt.principal_amount
    debt.status = payload.get("status") or "active"
    debt.counterparty_name = payload.get("counterparty_name")
    debt.counterparty_phone = payload.get("counterparty_phone")
    debt.confirmed = 1
    debt.insert(ignore_permissions=True)

    return {"status": "accepted", "debt": debt.name}


@frappe.whitelist(allow_guest=False)
def decline_request(request_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    request = frappe.get_doc("Hisabi Debt Request", request_id)
    user_phone = frappe.get_value("User", user, "phone")
    if request.user != user and user_phone and normalize_phone(user_phone) != normalize_phone(request.to_phone or ""):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    request.status = "declined"
    request.save(ignore_permissions=True)
    return {"status": "declined", "request_id": request.name}
