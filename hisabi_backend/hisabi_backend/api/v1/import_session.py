"""Import Session APIs — backend persistence for bank statement imports."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cint

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.wallet_acl import require_wallet_member
from hisabi_backend.utils.validators import validate_client_id


@frappe.whitelist(allow_guest=False)
def create_session(
    wallet_id: str,
    source_type: str = "csv",
    file_name: str = "",
    rows: Optional[List[Dict[str, Any]]] = None,
    client_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an import session with pending rows."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if isinstance(rows, str):
        rows = json.loads(rows)
    rows = rows or []

    resolved_client_id = (client_id or "").strip() or frappe.generate_hash(length=16)
    if frappe.db.exists("Hisabi Import Session", resolved_client_id):
        session = frappe.get_doc("Hisabi Import Session", resolved_client_id)
        return {"status": "ok", "session_id": session.name, "already_exists": True}

    session = frappe.new_doc("Hisabi Import Session")
    session.client_id = resolved_client_id
    session.name = resolved_client_id
    session.wallet_id = wallet_id
    session.user = user
    session.source_type = source_type
    session.file_name = (file_name or "").strip()[:255]
    session.status = "pending"
    session.total_rows = len(rows)
    session.imported_rows = 0
    session.skipped_rows = 0
    session.doc_version = 1
    session.server_modified = now_datetime()
    session.is_deleted = 0

    for i, row in enumerate(rows):
        session.append("import_rows", {
            "row_index": i,
            "raw_text": (row.get("raw_text") or "")[:500],
            "date": row.get("date"),
            "description": (row.get("description") or "")[:255],
            "amount": flt(row.get("amount") or 0),
            "currency": (row.get("currency") or "")[:10],
            "transaction_type": row.get("transaction_type") or "expense",
            "category": (row.get("category") or "")[:255],
            "account": (row.get("account") or "")[:255],
            "status": "pending",
            "ai_confidence": flt(row.get("ai_confidence") or 0),
            "ai_notes": (row.get("ai_notes") or "")[:500],
        })

    session.insert(ignore_permissions=True)
    return {"status": "ok", "session_id": session.name, "total_rows": len(rows)}


@frappe.whitelist(allow_guest=False)
def get_sessions(
    wallet_id: str,
    limit: int = 20,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List import sessions for a wallet (without rows)."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    sessions = frappe.get_all(
        "Hisabi Import Session",
        filters={"wallet_id": wallet_id, "user": user, "is_deleted": 0},
        fields=["name", "client_id", "source_type", "file_name", "status",
                "total_rows", "imported_rows", "skipped_rows", "confirmed_at",
                "creation", "modified"],
        order_by="modified desc",
        limit_page_length=cint(limit) or 20,
    )

    return {"status": "ok", "sessions": sessions, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def get_session(
    session_id: str,
    wallet_id: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a single import session with all rows."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    if not frappe.db.exists("Hisabi Import Session", session_id):
        frappe.throw(_("Import session not found"), frappe.DoesNotExistError)

    session = frappe.get_doc("Hisabi Import Session", session_id)
    if session.wallet_id != wallet_id or session.user != user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    rows = [
        {
            "row_index": r.row_index,
            "raw_text": r.raw_text,
            "date": str(r.date) if r.date else None,
            "description": r.description,
            "amount": r.amount,
            "currency": r.currency,
            "transaction_type": r.transaction_type,
            "category": r.category,
            "account": r.account,
            "status": r.status,
            "transaction_id": r.transaction_id,
            "ai_confidence": r.ai_confidence,
            "ai_notes": r.ai_notes,
        }
        for r in (session.import_rows or [])
    ]

    return {
        "status": "ok",
        "session": {
            "session_id": session.name,
            "wallet_id": session.wallet_id,
            "source_type": session.source_type,
            "file_name": session.file_name,
            "status": session.status,
            "total_rows": session.total_rows,
            "imported_rows": session.imported_rows,
            "skipped_rows": session.skipped_rows,
            "confirmed_at": session.confirmed_at,
        },
        "rows": rows,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def confirm_session(
    session_id: str,
    wallet_id: str,
    selected_row_indices: Optional[List[int]] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Confirm an import session: mark selected rows as imported."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if isinstance(selected_row_indices, str):
        selected_row_indices = json.loads(selected_row_indices)

    if not frappe.db.exists("Hisabi Import Session", session_id):
        frappe.throw(_("Import session not found"), frappe.DoesNotExistError)

    session = frappe.get_doc("Hisabi Import Session", session_id)
    if session.wallet_id != wallet_id or session.user != user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if session.status == "confirmed":
        return {"status": "ok", "already_confirmed": True, "session_id": session_id}
    if session.status == "cancelled":
        frappe.throw(_("Cannot confirm a cancelled session"), frappe.ValidationError)

    selected_set = set(selected_row_indices or [])
    imported = 0
    skipped = 0

    for row in session.import_rows or []:
        if selected_row_indices is None or row.row_index in selected_set:
            row.status = "imported"
            imported += 1
        else:
            row.status = "skipped"
            skipped += 1

    session.status = "confirmed"
    session.imported_rows = imported
    session.skipped_rows = skipped
    session.confirmed_at = now_datetime()
    session.save(ignore_permissions=True)

    return {
        "status": "ok",
        "session_id": session_id,
        "imported": imported,
        "skipped": skipped,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def cancel_session(
    session_id: str,
    wallet_id: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Cancel a pending import session."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not frappe.db.exists("Hisabi Import Session", session_id):
        frappe.throw(_("Import session not found"), frappe.DoesNotExistError)

    session = frappe.get_doc("Hisabi Import Session", session_id)
    if session.wallet_id != wallet_id or session.user != user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if session.status == "confirmed":
        frappe.throw(_("Cannot cancel a confirmed session"), frappe.ValidationError)

    session.status = "cancelled"
    session.save(ignore_permissions=True)

    return {"status": "ok", "session_id": session_id}
