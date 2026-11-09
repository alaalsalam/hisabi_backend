"""Debt network APIs (v1).

Peer-to-peer ("network") debts between two Hisabi users identified by phone. The loop is:
lookup -> create request -> recipient lists incoming -> accept (materialises a synced debt) / decline.

Phone matching is done on DIGITS ONLY. `register_user` stores `User.phone`/`mobile_no` as bare digits
(no leading '+'), while `normalize_phone` preserves a leading '+', so comparing normalized values
directly silently fails to match real users. `_phone_digits` is the single canonical matching form.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import flt

from hisabi_backend.utils.notifications import create_user_notification
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user

MIN_PHONE_DIGITS = 8
_DIRECTIONS = ("owe", "owed_to_me")
_INVERT_DIRECTION = {"owe": "owed_to_me", "owed_to_me": "owe"}


def _phone_digits(phone: Optional[str]) -> str:
    """Canonical phone-matching form: digits only, no '+', no separators."""
    if not phone:
        return ""
    return re.sub(r"\D", "", str(phone))


def _phone_variants(digits: str) -> List[str]:
    return list(dict.fromkeys([digits, f"+{digits}"]))


def _find_user_by_phone_digits(digits: str) -> Optional[str]:
    if not digits:
        return None
    variants = _phone_variants(digits)
    for field in ("phone", "mobile_no"):
        name = frappe.db.get_value("User", {field: ["in", variants]}, "name")
        if name:
            return name
    return None


def _my_phone_digits(user: str) -> str:
    return _phone_digits(frappe.db.get_value("User", user, "phone"))


def _coerce_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, str):
        payload = frappe.parse_json(payload) or {}
    if not isinstance(payload, dict):
        frappe.throw(_("payload is required"), frappe.ValidationError)
    return payload


@frappe.whitelist(allow_guest=False)
def user_lookup_by_phone(phone: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    """Return whether a registered Hisabi user exists for a phone.

    Intentionally minimal (found + display_name only): this endpoint is a user-enumeration surface,
    so it never leaks the account id/email.
    """
    require_device_token_auth()
    digits = _phone_digits(phone)
    if len(digits) < MIN_PHONE_DIGITS:
        frappe.throw(_("phone is invalid"), frappe.ValidationError)

    match = _find_user_by_phone_digits(digits)
    if not match:
        return {"found": False, "display_name": None}
    return {"found": True, "display_name": frappe.db.get_value("User", match, "full_name") or None}


@frappe.whitelist(allow_guest=False)
def create_network_request(
    to_phone: str,
    payload: Dict[str, Any],
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    payload = _coerce_payload(payload)

    to_digits = _phone_digits(to_phone)
    if len(to_digits) < MIN_PHONE_DIGITS:
        frappe.throw(_("to_phone is invalid"), frappe.ValidationError)

    direction = (payload.get("direction") or "owe").strip()
    if direction not in _DIRECTIONS:
        frappe.throw(_("direction is invalid"), frappe.ValidationError)

    amount = flt(payload.get("principal_amount") or payload.get("amount") or 0)
    if amount <= 0:
        frappe.throw(_("amount must be greater than zero"), frappe.ValidationError)

    from_phone = _my_phone_digits(user) or user

    request = frappe.new_doc("Hisabi Debt Request")
    request.user = user
    # wallet-scoped doctype: needs the sender's wallet for scope validation + sync fan-out.
    request.wallet_id = ensure_default_wallet_for_user(user, device_id)
    request.client_id = payload.get("client_id") or frappe.generate_hash(length=12)
    request.name = request.client_id
    request.from_phone = from_phone
    request.to_phone = to_digits
    request.debt_payload_json = {
        "direction": direction,
        "currency": payload.get("currency"),
        "principal_amount": amount,
        "note": payload.get("note"),
        "counterparty_name": payload.get("counterparty_name"),
        "counterparty_phone": to_digits,
        "client_id": request.client_id,
    }
    request.status = "pending"
    apply_common_sync_fields(request, bump_version=True, mark_deleted=False)
    request.insert(ignore_permissions=True)

    return {"status": "ok", "request_id": request.name}


def _serialize_requests(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sender_ids = list({r.get("user") for r in rows if r.get("user")})
    names = {}
    if sender_ids:
        for u in frappe.get_all(
            "User", filters={"name": ["in", sender_ids]}, fields=["name", "full_name"]
        ):
            names[u.name] = u.full_name

    out = []
    for r in rows:
        payload = r.get("debt_payload_json")
        if isinstance(payload, str):
            payload = frappe.parse_json(payload) or {}
        payload = payload or {}
        out.append(
            {
                "request_id": r.get("name"),
                "status": r.get("status"),
                "from_phone": r.get("from_phone"),
                "to_phone": r.get("to_phone"),
                "from_name": names.get(r.get("user")) or r.get("from_phone"),
                "direction": payload.get("direction"),
                "amount": payload.get("principal_amount"),
                "currency": payload.get("currency"),
                "note": payload.get("note"),
                "counterparty_name": payload.get("counterparty_name"),
                "created": str(r.get("creation")) if r.get("creation") else None,
            }
        )
    return out


@frappe.whitelist(allow_guest=False)
def debt_requests_list(direction: str = "incoming", device_id: Optional[str] = None) -> Dict[str, Any]:
    """List debt requests. `incoming` = addressed to my phone; `outgoing` = created by me."""
    user, _device = require_device_token_auth()

    if direction == "outgoing":
        filters = {"user": user, "is_deleted": 0}
    else:
        my_digits = _my_phone_digits(user)
        if not my_digits:
            return {"requests": []}
        filters = {"to_phone": ["in", _phone_variants(my_digits)], "is_deleted": 0}

    rows = frappe.get_all(
        "Hisabi Debt Request",
        filters=filters,
        fields=["name", "user", "from_phone", "to_phone", "status", "debt_payload_json", "creation"],
        order_by="creation desc",
        limit=50,
    )
    requests = _serialize_requests(rows)
    status_rank = {"pending": 0, "accepted": 1, "rejected": 2}
    requests.sort(key=lambda r: status_rank.get(r.get("status"), 3))
    return {"requests": requests}


@frappe.whitelist(allow_guest=False)
def accept_request(request_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    request = frappe.get_doc("Hisabi Debt Request", request_id)

    if request.is_deleted:
        frappe.throw(_("Request is deleted"), frappe.ValidationError)

    # Only the recipient (a user whose phone matches to_phone) may accept.
    my_digits = _my_phone_digits(user)
    if not my_digits or my_digits != _phone_digits(request.to_phone):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if request.status == "rejected":
        frappe.throw(_("Request already declined"), frappe.ValidationError)

    debt_client_id = f"debtreq-{request.name}"
    if request.status == "accepted" or frappe.db.exists("Hisabi Debt", debt_client_id):
        return {"status": "accepted", "debt": debt_client_id, "already": True}

    payload = request.debt_payload_json or request.debt_payload or {}
    if isinstance(payload, str):
        payload = frappe.parse_json(payload) or {}
    payload = payload or {}

    request.status = "accepted"
    apply_common_sync_fields(request, bump_version=True, mark_deleted=False)
    request.flags.ignore_wallet_scope = True  # request is scoped to the sender's wallet, not the recipient's
    request.save(ignore_permissions=True)

    # The recipient's debt mirrors the request from the OTHER side: direction inverted, and the
    # counterparty is the SENDER (not the payload's counterparty, which is the recipient themselves).
    sender_name = frappe.db.get_value("User", request.user, "full_name") or request.from_phone or "Unknown"
    recipient_direction = _INVERT_DIRECTION.get(payload.get("direction") or "owe", "owe")
    amount = flt(payload.get("principal_amount") or payload.get("amount") or 0)
    wallet_id = ensure_default_wallet_for_user(user, device_id)

    debt = frappe.new_doc("Hisabi Debt")
    debt.user = user
    debt.wallet_id = wallet_id
    debt.client_id = debt_client_id
    debt.name = debt_client_id
    debt.debt_name = sender_name
    debt.direction = recipient_direction
    debt.currency = payload.get("currency")
    debt.principal_amount = amount
    debt.remaining_amount = amount
    debt.status = "active"
    debt.counterparty_type = "user_phone"
    debt.counterparty_name = sender_name
    debt.counterparty_phone = request.from_phone
    debt.confirmed = 1
    apply_common_sync_fields(debt, bump_version=True, mark_deleted=False)
    debt.insert(ignore_permissions=True)

    # Notify the original sender that their request was accepted.
    accepter_name = frappe.db.get_value("User", user, "full_name") or ""
    create_user_notification(
        request.user,
        notif_type="debt_request_response",
        title="تم قبول طلب الدين",
        body=f"{accepter_name} قبل طلب الدين المشترك".strip(),
        action_route="/debts?tab=network",
        ref_doctype="Hisabi Debt Request",
        ref_name=request.name,
        from_user=user,
        from_name=accepter_name,
    )

    return {"status": "accepted", "debt": debt.name, "wallet_id": wallet_id}


@frappe.whitelist(allow_guest=False)
def decline_request(request_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    request = frappe.get_doc("Hisabi Debt Request", request_id)

    if request.is_deleted:
        frappe.throw(_("Request is deleted"), frappe.ValidationError)

    # The recipient may decline; the creator may cancel their own pending request.
    my_digits = _my_phone_digits(user)
    is_recipient = bool(my_digits) and my_digits == _phone_digits(request.to_phone)
    is_creator = request.user == user
    if not (is_recipient or is_creator):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if request.status == "accepted":
        frappe.throw(_("Request already accepted"), frappe.ValidationError)

    request.status = "rejected"
    apply_common_sync_fields(request, bump_version=True, mark_deleted=False)
    request.flags.ignore_wallet_scope = True  # request is scoped to the sender's wallet, not the recipient's
    request.save(ignore_permissions=True)

    # Notify the sender only when the recipient (not the creator cancelling) declined.
    if is_recipient and request.user != user:
        decliner_name = frappe.db.get_value("User", user, "full_name") or ""
        create_user_notification(
            request.user,
            notif_type="debt_request_response",
            title="تم رفض طلب الدين",
            body=f"{decliner_name} رفض طلب الدين المشترك".strip(),
            action_route="/debts?tab=network",
            ref_doctype="Hisabi Debt Request",
            ref_name=request.name,
            from_user=user,
            from_name=decliner_name,
        )
    return {"status": "declined", "request_id": request.name}
