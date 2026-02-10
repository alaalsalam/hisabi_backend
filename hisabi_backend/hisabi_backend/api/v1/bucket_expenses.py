"""Expense-to-bucket assignment APIs (v1)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import frappe
from frappe.utils import now_datetime

from hisabi_backend.utils.bucket_allocations import (
    InvalidBucketExpenseAssignmentError,
    build_invalid_bucket_expense_assignment_response,
    ensure_bucket_wallet_scope,
    ensure_expense_transaction,
    raise_invalid_bucket_expense_assignment,
)
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member

DOCTYPE = "Hisabi Transaction Bucket Expense"


def _to_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    dt = frappe.utils.get_datetime(value)
    return dt.isoformat() if dt else None


def _minimal_server_record(doc: frappe.model.document.Document) -> Dict[str, Any]:
    stable_name = doc.client_id or doc.name
    return {
        "name": stable_name,
        "client_id": doc.client_id,
        "doc_version": doc.doc_version,
        "server_modified": _to_iso(doc.server_modified),
        "is_deleted": doc.is_deleted,
        "deleted_at": _to_iso(doc.deleted_at),
    }


def _conflict_response(
    doc: frappe.model.document.Document,
    *,
    client_base_version: int | None,
) -> Dict[str, Any]:
    server_doc = _minimal_server_record(doc)
    return {
        "status": "conflict",
        "entity_type": DOCTYPE,
        "entity_id": doc.client_id,
        "client_base_version": client_base_version,
        "server_doc": server_doc,
        "server_doc_version": doc.doc_version,
        # Backward-compatible aliases for existing clients.
        "client_id": doc.client_id,
        "doc_version": doc.doc_version,
        "server_modified": _to_iso(doc.server_modified),
        "server_record": server_doc,
    }


def _ledger_op_id(wallet_id: str, op_id: str) -> str:
    return f"{wallet_id}:{op_id}"


def _check_duplicate_op(user: str, device_id: str, wallet_id: str, op_id: str) -> bool:
    if not device_id or not op_id:
        return False
    return bool(
        frappe.db.exists(
            "Hisabi Sync Op",
            {"user": user, "device_id": device_id, "op_id": _ledger_op_id(wallet_id, op_id)},
        )
    )


def _get_op_result(user: str, device_id: str, wallet_id: str, op_id: str) -> Optional[Dict[str, Any]]:
    if not device_id or not op_id:
        return None
    result_json = frappe.get_value(
        "Hisabi Sync Op",
        {"user": user, "device_id": device_id, "op_id": _ledger_op_id(wallet_id, op_id)},
        "result_json",
    )
    if not result_json:
        return None
    try:
        return json.loads(result_json)
    except json.JSONDecodeError:
        return None


def _store_op_result(
    *,
    user: str,
    device_id: str,
    wallet_id: str,
    op_id: str,
    entity_client_id: str,
    status: str,
    result: Dict[str, Any],
) -> None:
    if not device_id or not op_id:
        return

    ledger_op_id = _ledger_op_id(wallet_id, op_id)
    if frappe.db.exists("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": ledger_op_id}):
        return

    sync_op = frappe.new_doc("Hisabi Sync Op")
    sync_op.user = user
    sync_op.device_id = device_id
    sync_op.op_id = ledger_op_id
    sync_op.entity_type = DOCTYPE
    sync_op.client_id = entity_client_id
    sync_op.status = status
    sync_op.result_json = json.dumps(result, ensure_ascii=False)
    if result.get("server_modified"):
        sync_op.server_modified = result.get("server_modified")
    sync_op.save(ignore_permissions=True)


def _resolve_tx_and_bucket(
    *,
    wallet_id: str,
    transaction_id: str,
    bucket_id: str,
) -> tuple[frappe.model.document.Document, frappe.model.document.Document]:
    tx = ensure_expense_transaction(transaction_id, wallet_id)
    bucket = ensure_bucket_wallet_scope(
        bucket_id,
        wallet_id,
        raise_error=raise_invalid_bucket_expense_assignment,
    )
    return tx, bucket


def _build_default_client_id(transaction_id: str) -> str:
    tx_id = str(transaction_id or "").strip()
    if tx_id:
        hashed = frappe.generate_hash(tx_id, 12)
        return f"txbexp-{hashed}"
    return f"txbexp-{frappe.generate_hash(length=12)}"


def _existing_active_assignment(wallet_id: str, transaction_name: str) -> Optional[frappe.model.document.Document]:
    name = frappe.get_value(
        DOCTYPE,
        {
            "wallet_id": wallet_id,
            "transaction_id": transaction_name,
            "is_deleted": 0,
        },
        "name",
    )
    return frappe.get_doc(DOCTYPE, name) if name else None


def _parse_base_version(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except Exception:
        raise_invalid_bucket_expense_assignment("base_version must be a number.")
    if parsed < 0:
        raise_invalid_bucket_expense_assignment("base_version must be a number.")
    return parsed


@frappe.whitelist(allow_guest=False)
def set(
    transaction_id: str,
    bucket_id: str,
    wallet_id: Optional[str] = None,
    client_id: Optional[str] = None,
    op_id: Optional[str] = None,
    base_version: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Set or replace a single expense-bucket assignment for a transaction."""
    try:
        user, device = require_device_token_auth()
        if not wallet_id:
            raise_invalid_bucket_expense_assignment("wallet_id is required.")
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")

        if not transaction_id:
            raise_invalid_bucket_expense_assignment("transaction_id is required.")
        if not bucket_id:
            raise_invalid_bucket_expense_assignment("bucket_id is required.")

        op_id_str = (op_id or "").strip()
        if op_id_str and _check_duplicate_op(user, device.device_id, wallet_id, op_id_str):
            stored = _get_op_result(user, device.device_id, wallet_id, op_id_str)
            if stored:
                replayed = dict(stored)
                replayed["already_applied"] = True
                return replayed

        tx_doc, bucket_doc = _resolve_tx_and_bucket(
            wallet_id=wallet_id,
            transaction_id=transaction_id,
            bucket_id=bucket_id,
        )
        parsed_base_version = _parse_base_version(base_version)
        existing = _existing_active_assignment(wallet_id, tx_doc.name)

        if existing and parsed_base_version is not None and int(existing.doc_version or 0) != parsed_base_version:
            result = _conflict_response(existing, client_base_version=parsed_base_version)
            if op_id_str:
                _store_op_result(
                    user=user,
                    device_id=device.device_id,
                    wallet_id=wallet_id,
                    op_id=op_id_str,
                    entity_client_id=existing.client_id or existing.name,
                    status="conflict",
                    result=result,
                )
            return result

        assignment = existing or frappe.new_doc(DOCTYPE)
        if not existing:
            resolved_client_id = (client_id or "").strip() or _build_default_client_id(tx_doc.client_id or tx_doc.name)
            resolved_client_id = validate_client_id(resolved_client_id)
            assignment.client_id = resolved_client_id
            assignment.name = resolved_client_id
            assignment.flags.name_set = True

        assignment.user = user
        assignment.wallet_id = wallet_id
        assignment.transaction_id = tx_doc.name
        assignment.bucket_id = bucket_doc.name
        apply_common_sync_fields(assignment, bump_version=True, mark_deleted=False)
        assignment.save(ignore_permissions=True)

        result = {
            "status": "ok",
            "transaction_id": tx_doc.client_id or tx_doc.name,
            "bucket_id": bucket_doc.client_id or bucket_doc.name,
            "assignment": {
                "client_id": assignment.client_id,
                "entity_id": assignment.client_id,
                "wallet_id": assignment.wallet_id,
                "transaction_id": assignment.transaction_id,
                "bucket_id": assignment.bucket_id,
                "doc_version": assignment.doc_version,
                "server_modified": _to_iso(assignment.server_modified),
                "is_deleted": assignment.is_deleted,
                "deleted_at": _to_iso(assignment.deleted_at),
            },
            "server_time": now_datetime().isoformat(),
        }
        if op_id_str:
            _store_op_result(
                user=user,
                device_id=device.device_id,
                wallet_id=wallet_id,
                op_id=op_id_str,
                entity_client_id=assignment.client_id,
                status="accepted",
                result=result,
            )
        return result
    except InvalidBucketExpenseAssignmentError as exc:
        frappe.clear_last_message()
        return build_invalid_bucket_expense_assignment_response(str(exc))


@frappe.whitelist(allow_guest=False)
def clear(
    transaction_id: str,
    wallet_id: Optional[str] = None,
    op_id: Optional[str] = None,
    base_version: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Clear expense-bucket assignment for a transaction."""
    try:
        user, device = require_device_token_auth()
        if not wallet_id:
            raise_invalid_bucket_expense_assignment("wallet_id is required.")
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")
        if not transaction_id:
            raise_invalid_bucket_expense_assignment("transaction_id is required.")

        op_id_str = (op_id or "").strip()
        if op_id_str and _check_duplicate_op(user, device.device_id, wallet_id, op_id_str):
            stored = _get_op_result(user, device.device_id, wallet_id, op_id_str)
            if stored:
                replayed = dict(stored)
                replayed["already_applied"] = True
                return replayed

        tx_doc = ensure_expense_transaction(transaction_id, wallet_id)
        parsed_base_version = _parse_base_version(base_version)
        existing = _existing_active_assignment(wallet_id, tx_doc.name)

        if not existing:
            result = {
                "status": "ok",
                "transaction_id": tx_doc.client_id or tx_doc.name,
                "cleared": False,
                "server_time": now_datetime().isoformat(),
            }
            if op_id_str:
                _store_op_result(
                    user=user,
                    device_id=device.device_id,
                    wallet_id=wallet_id,
                    op_id=op_id_str,
                    entity_client_id=f"txbexp-{tx_doc.client_id or tx_doc.name}",
                    status="accepted",
                    result=result,
                )
            return result

        if parsed_base_version is not None and int(existing.doc_version or 0) != parsed_base_version:
            result = _conflict_response(existing, client_base_version=parsed_base_version)
            if op_id_str:
                _store_op_result(
                    user=user,
                    device_id=device.device_id,
                    wallet_id=wallet_id,
                    op_id=op_id_str,
                    entity_client_id=existing.client_id or existing.name,
                    status="conflict",
                    result=result,
                )
            return result

        apply_common_sync_fields(existing, bump_version=True, mark_deleted=True)
        if existing.meta.has_field("deleted_at") and not existing.deleted_at:
            existing.deleted_at = now_datetime()
        existing.save(ignore_permissions=True)

        result = {
            "status": "ok",
            "transaction_id": tx_doc.client_id or tx_doc.name,
            "cleared": True,
            "assignment": {
                "client_id": existing.client_id,
                "entity_id": existing.client_id,
                "doc_version": existing.doc_version,
                "server_modified": _to_iso(existing.server_modified),
                "is_deleted": existing.is_deleted,
                "deleted_at": _to_iso(existing.deleted_at),
            },
            "server_time": now_datetime().isoformat(),
        }
        if op_id_str:
            _store_op_result(
                user=user,
                device_id=device.device_id,
                wallet_id=wallet_id,
                op_id=op_id_str,
                entity_client_id=existing.client_id or existing.name,
                status="accepted",
                result=result,
            )
        return result
    except InvalidBucketExpenseAssignmentError as exc:
        frappe.clear_last_message()
        return build_invalid_bucket_expense_assignment_response(str(exc))
