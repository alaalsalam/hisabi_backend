"""Sync endpoints (v1)."""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime
from werkzeug.wrappers import Response
from hisabi_backend.domain.recalc_engine import (
    recalc_account_balance,
    recalc_budgets,
    recalc_debts,
    recalc_goals,
    recalc_jameyas,
)
from hisabi_backend.utils.security import require_device_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import require_wallet_member
from hisabi_backend.utils.validators import (
    ensure_entity_id_matches,
    ensure_link_ownership,
    validate_client_id,
)


DOCTYPE_LIST = [
    "Hisabi Settings",
    "Hisabi Device",
    "Hisabi Wallet",
    "Hisabi Wallet Member",
    "Hisabi Account",
    "Hisabi Category",
    "Hisabi Transaction",
    "Hisabi Attachment",
    "Hisabi Bucket",
    "Hisabi Allocation Rule",
    "Hisabi Allocation Rule Line",
    "Hisabi Transaction Allocation",
    "Hisabi Budget",
    "Hisabi Goal",
    "Hisabi Debt",
    "Hisabi Debt Installment",
    "Hisabi Debt Request",
    "Hisabi Jameya",
    "Hisabi Jameya Payment",
    "Hisabi FX Rate",
    "Hisabi Custom Currency",
    "Hisabi Audit Log",
]

SYNC_PUSH_ALLOWLIST = {
    "Hisabi Wallet",
    "Hisabi Wallet Member",
    "Hisabi Account",
    "Hisabi Category",
    "Hisabi Transaction",
    "Hisabi Debt",
    "Hisabi Debt Installment",
    "Hisabi Debt Request",
    "Hisabi Budget",
    "Hisabi Goal",
    "Hisabi Bucket",
    "Hisabi Allocation Rule",
    "Hisabi Allocation Rule Line",
    "Hisabi Transaction Allocation",
    "Hisabi Jameya",
    "Hisabi Jameya Payment",
    "Hisabi Attachment",
}

# Security: prevent client from writing unexpected fields / doctypes through sync.
SYNC_PUSH_ALLOWED_FIELDS = {
    "Hisabi Wallet": {
        "client_id",
        "wallet_id",
        "wallet_name",
        "status",
        "client_created_ms",
        "client_modified_ms",
    },
    "Hisabi Wallet Member": {
        "client_id",
        "wallet_id",
        "wallet",
        "user",
        "role",
        "status",
        "joined_at",
        "removed_at",
        "client_created_ms",
        "client_modified_ms",
    },
    "Hisabi Account": {
        "client_id",
        "wallet_id",
        "account_name",
        "account_type",
        "currency",
        "opening_balance",
        "color",
        "icon",
        "archived",
        "sort_order",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Category": {
        "client_id",
        "wallet_id",
        "category_name",
        "kind",
        "parent_category",
        "color",
        "icon",
        "archived",
        "sort_order",
        "default_bucket",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Transaction": {
        "client_id",
        "wallet_id",
        "transaction_type",
        "date_time",
        "amount",
        "currency",
        "account",
        "to_account",
        "category",
        "bucket",
        "note",
        "amount_base",
        "fx_rate_used",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Debt": {
        "client_id",
        "wallet_id",
        "debt_name",
        "direction",
        "currency",
        "principal_amount",
        "counterparty_name",
        "counterparty_type",
        "counterparty_phone",
        "confirmed",
        "note",
        "due_date",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Debt Installment": {
        "client_id",
        "wallet_id",
        "debt",
        "due_date",
        "amount",
        "status",
        "paid_at",
        "paid_amount",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Debt Request": {
        "client_id",
        "wallet_id",
        "from_phone",
        "to_phone",
        "debt_payload",
        "debt_payload_json",
        "status",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Budget": {
        "client_id",
        "wallet_id",
        "budget_name",
        "period",
        "scope_type",
        "category",
        "currency",
        "amount",
        "amount_base",
        "start_date",
        "end_date",
        "alert_threshold",
        "archived",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Goal": {
        "client_id",
        "wallet_id",
        "goal_name",
        "goal_type",
        "currency",
        "target_amount",
        "target_amount_base",
        "target_date",
        "linked_account",
        "linked_debt",
        "status",
        "color",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Bucket": {
        "client_id",
        "wallet_id",
        "bucket_name",
        "color",
        "icon",
        "sort_order",
        "archived",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Allocation Rule": {
        "client_id",
        "wallet_id",
        "rule_name",
        "is_default",
        "scope_type",
        "scope_ref",
        "active",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Allocation Rule Line": {
        "client_id",
        "wallet_id",
        "rule",
        "bucket",
        "percent",
        "sort_order",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Transaction Allocation": {
        "client_id",
        "wallet_id",
        "transaction",
        "bucket",
        "percent",
        "amount",
        "currency",
        "amount_base",
        "rule_used",
        "is_manual_override",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Jameya": {
        "client_id",
        "wallet_id",
        "jameya_name",
        "currency",
        "monthly_amount",
        "total_members",
        "my_turn",
        "total_amount",
        "period",
        "start_date",
        "status",
        "note",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Jameya Payment": {
        "client_id",
        "wallet_id",
        "jameya",
        "period_number",
        "due_date",
        "amount",
        "status",
        "paid_at",
        "is_my_turn",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Attachment": {
        "client_id",
        "wallet_id",
        "owner_entity_type",
        "owner_client_id",
        "file_id",
        "file_url",
        "file_name",
        "mime_type",
        "file_size",
        "sha256",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
}

SYNC_PUSH_REQUIRED_FIELDS_CREATE = {
    "Hisabi Wallet": {"wallet_name", "status"},
    "Hisabi Wallet Member": {"wallet", "user", "role", "status"},
    "Hisabi Account": {"account_name", "account_type", "currency"},
    "Hisabi Category": {"category_name", "kind"},
    "Hisabi Transaction": {"transaction_type", "date_time", "amount", "currency", "account"},
    "Hisabi Debt": {"debt_name", "direction", "principal_amount"},
    "Hisabi Debt Installment": {"debt", "amount"},
    "Hisabi Debt Request": set(),
    "Hisabi Budget": {"budget_name", "period", "scope_type"},
    "Hisabi Goal": {"goal_name", "goal_type"},
    "Hisabi Bucket": {"bucket_name"},
    "Hisabi Allocation Rule": {"rule_name", "scope_type"},
    "Hisabi Allocation Rule Line": {"rule", "bucket"},
    "Hisabi Transaction Allocation": {"transaction", "bucket"},
    "Hisabi Jameya": {"jameya_name", "monthly_amount", "total_members", "my_turn", "start_date"},
    "Hisabi Jameya Payment": {"jameya"},
    "Hisabi Attachment": {"owner_entity_type", "owner_client_id", "file_mime", "file_size"},
}

SYNC_PUSH_REQUIRED_FIELD_GROUPS = {
    "Hisabi Budget": [{"amount", "amount_base"}],
    "Hisabi Goal": [{"target_amount", "target_amount_base"}],
}

SYNC_PUSH_FIELD_TYPES = {
    "wallet_name": "string",
    "status": "string",
    "wallet": "string",
    "user": "string",
    "role": "string",
    "account_name": "string",
    "account_type": "string",
    "currency": "string",
    "category_name": "string",
    "kind": "string",
    "transaction_type": "string",
    "date_time": "string",
    "account": "string",
    "debt_name": "string",
    "direction": "string",
    "budget_name": "string",
    "period": "string",
    "scope_type": "string",
    "goal_name": "string",
    "goal_type": "string",
    "bucket_name": "string",
    "rule_name": "string",
    "transaction": "string",
    "jameya_name": "string",
    "start_date": "string",
    "owner_entity_type": "string",
    "owner_client_id": "string",
    "file_mime": "string",
    "amount": "number",
    "amount_base": "number",
    "principal_amount": "number",
    "target_amount": "number",
    "target_amount_base": "number",
    "monthly_amount": "number",
    "total_members": "number",
    "my_turn": "number",
    "file_size": "number",
    "fx_rate_used": "number",
}

SYNC_PAYLOAD_LOG_IGNORE_KEYS = {"id"}

RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW_SEC = 600
MAX_PUSH_ITEMS = 200
MAX_PAYLOAD_BYTES = 100 * 1024

SERVER_AUTH_FIELDS = {
    "Hisabi Account": {"current_balance"},
    "Hisabi Budget": {"spent_amount"},
    "Hisabi Goal": {"current_amount", "progress_percent", "remaining_amount"},
    "Hisabi Debt": {"remaining_amount", "status"},
    "Hisabi Jameya": {"status", "total_amount"},
}

FIELD_MAP = {
    "Hisabi Account": {
        "name": "account_name",
        "title": "account_name",
        "type": "account_type",
    },
    "Hisabi Category": {
        "name": "category_name",
        "title": "category_name",
        "parent_id": "parent_category",
        "default_bucket_id": "default_bucket",
    },
    "Hisabi Bucket": {"name": "bucket_name", "title": "bucket_name"},
    "Hisabi Allocation Rule": {
        "name": "rule_name",
        "title": "rule_name",
        "scope_ref_id": "scope_ref",
    },
    "Hisabi Allocation Rule Line": {
        "rule_id": "rule",
        "bucket_id": "bucket",
    },
    "Hisabi Transaction Allocation": {
        "transaction_id": "transaction",
        "bucket_id": "bucket",
        "rule_id_used": "rule_used",
    },
    "Hisabi Budget": {
        "name": "budget_name",
        "title": "budget_name",
        "category_id": "category",
    },
    "Hisabi Goal": {
        "name": "goal_name",
        "title": "goal_name",
        "type": "goal_type",
        "linked_account_id": "linked_account",
        "linked_debt_id": "linked_debt",
    },
    "Hisabi Debt": {"name": "debt_name", "title": "debt_name"},
    "Hisabi Jameya": {"name": "jameya_name", "title": "jameya_name"},
    "Hisabi Transaction": {
        "type": "transaction_type",
        "account_id": "account",
        "to_account_id": "to_account",
        "category_id": "category",
        "bucket_id": "bucket",
    },
}


def _require_device_auth(device_id: str) -> Tuple[str, frappe.model.document.Document]:
    return require_device_auth(device_id)


def _get_doc_by_client_id(
    doctype: str, user: str, client_id: str, *, wallet_id: str | None = None
) -> Optional[frappe.model.document.Document]:
    meta = frappe.get_meta(doctype)
    filters: Dict[str, Any] = {"client_id": client_id}
    # Shared wallet: authoritative scoping is wallet_id, not user.
    if meta.has_field("wallet_id") and wallet_id:
        filters["wallet_id"] = wallet_id
    elif meta.has_field("user"):
        filters["user"] = user
    else:
        filters["owner"] = user

    name = frappe.get_value(doctype, filters)
    if not name:
        return None
    return frappe.get_doc(doctype, name)


def _set_owner(doc: frappe.model.document.Document, user: str) -> None:
    # Keep creator/owner stable once created.
    if doc.is_new():
        if doc.meta.has_field("user"):
            doc.user = user
        else:
            doc.owner = user


def _strip_server_auth_fields(doctype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    payload = dict(payload)
    for field in SERVER_AUTH_FIELDS.get(doctype, set()):
        payload.pop(field, None)
    return payload


def _apply_field_map(doctype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    payload = dict(payload)
    for old, new in FIELD_MAP.get(doctype, {}).items():
        if old in payload and new not in payload:
            payload[new] = payload.pop(old)
    return payload


def _resolve_base_currency(wallet_id: str, user: str) -> str | None:
    currency = frappe.get_value(
        "Hisabi Settings",
        {"wallet_id": wallet_id, "is_deleted": 0},
        "base_currency",
    )
    if not currency:
        currency = frappe.get_value(
            "Hisabi Settings",
            {"user": user, "is_deleted": 0},
            "base_currency",
        )
    if not currency:
        currency = frappe.db.get_single_value("System Settings", "currency")
    return currency


def _filter_payload_fields(doc: frappe.model.document.Document, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    allowed = {field.fieldname for field in doc.meta.fields}
    return {key: value for key, value in payload.items() if key in allowed}


def _unknown_payload_fields(doctype: str, payload: Dict[str, Any]) -> List[str]:
    if not payload:
        return []
    allowed = SYNC_PUSH_ALLOWED_FIELDS.get(doctype, set())
    if not allowed:
        return []
    return sorted(
        [key for key in payload.keys() if key not in allowed and key not in SYNC_PAYLOAD_LOG_IGNORE_KEYS]
    )


def _store_op_id(
    *,
    user: str,
    device_id: str,
    op_id: str,
    entity_type: str,
    entity_client_id: str,
    status: str,
    payload: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    if not device_id or not op_id:
        return

    if frappe.db.exists("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": op_id}):
        return

    sync_op = frappe.new_doc("Hisabi Sync Op")
    sync_op.user = user
    sync_op.device_id = device_id
    sync_op.op_id = op_id
    sync_op.entity_type = entity_type
    sync_op.client_id = entity_client_id
    sync_op.status = status
    sync_op.result_json = json.dumps(result, ensure_ascii=False)
    if result.get("server_modified"):
        sync_op.server_modified = result.get("server_modified")
    sync_op.save(ignore_permissions=True)


def _check_duplicate_op(user: str, device_id: str, op_id: str) -> bool:
    if not device_id or not op_id:
        return False
    return bool(frappe.db.exists("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": op_id}))


def _get_op_status(user: str, device_id: str, op_id: str) -> Optional[str]:
    if not device_id or not op_id:
        return None
    return frappe.get_value("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": op_id}, "status")


def _recalculate_account_balance(user: str, account_name: str, *, wallet_id: str | None = None) -> None:
    recalc_account_balance(user, account_name, wallet_id=wallet_id)


def _ensure_supported_doctype(doctype: str) -> None:
    if doctype not in DOCTYPE_LIST:
        frappe.throw(_("Unsupported entity_type: {0}").format(doctype), frappe.ValidationError)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(_("DocType not installed: {0}").format(doctype), frappe.ValidationError)
    frappe.get_meta(doctype)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _invalid_field_types(payload: Dict[str, Any], fields: set[str]) -> Dict[str, str]:
    invalid: Dict[str, str] = {}
    for field in fields:
        expected = SYNC_PUSH_FIELD_TYPES.get(field)
        if not expected or field not in payload:
            continue
        value = payload.get(field)
        if expected == "string" and not isinstance(value, str):
            invalid[field] = "string"
        elif expected == "number" and not _is_number(value):
            invalid[field] = "number"
    return invalid


ERROR_MESSAGE_MAP = {
    "entity_type_required": "entity_type is required",
    "unsupported_entity_type": "unsupported_entity_type",
    "doctype_not_installed": "doctype_not_installed",
    "invalid_operation": "invalid operation",
    "entity_id_required": "entity_id is required",
    "payload_must_be_object": "payload must be an object",
    "wallet_id_mismatch": "wallet_id mismatch",
    "entity_id_mismatch": "entity_id does not match payload client_id",
    "invalid_client_id": "invalid client_id",
    "base_version_required": "base_version is required",
    "base_version_invalid": "base_version must be a number",
    "missing_required_fields": "missing required fields",
    "invalid_field": "invalid field",
    "invalid_field_type": "invalid field type",
    "not_found": "record not found",
    "payload_too_large": "payload too large",
    "wallet_id_must_equal_client_id": "wallet_id must equal client_id",
    "wallet_access_denied": "wallet access denied",
}


def _build_item_error(
    *,
    error_code: str,
    entity_type: Optional[str] = None,
    client_id: Optional[str] = None,
    detail: Any = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "error",
        "error": error_code,
        "error_code": error_code,
        "error_message": ERROR_MESSAGE_MAP.get(error_code, error_code),
    }
    if entity_type:
        payload["entity_type"] = entity_type
    if client_id:
        payload["client_id"] = client_id
    if detail is not None:
        payload["detail"] = detail
    return payload


def _build_sync_response(payload: Dict[str, Any], status_code: int = 200) -> Response:
    response = Response()
    response.mimetype = "application/json"
    response.status_code = status_code
    response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return response


def _build_sync_error(error_code: str, message: str, *, status_code: int = 417) -> Response:
    return _build_sync_response({"error": error_code, "message": message}, status_code=status_code)


def _sync_status_for_exception(exc: Exception) -> int:
    if isinstance(exc, frappe.AuthenticationError):
        return 401
    if isinstance(exc, frappe.PermissionError):
        return 403
    if isinstance(exc, frappe.ValidationError):
        return 417
    return 500


def _validate_sync_push_item(item: Dict[str, Any], wallet_id: str) -> Optional[Dict[str, Any]]:
    entity_type = item.get("entity_type")
    if not entity_type:
        return _build_item_error(error_code="entity_type_required")

    if entity_type not in SYNC_PUSH_ALLOWLIST:
        return _build_item_error(error_code="unsupported_entity_type", entity_type=entity_type)

    if not frappe.db.exists("DocType", entity_type):
        return _build_item_error(error_code="doctype_not_installed", entity_type=entity_type)

    operation = item.get("operation")
    if operation not in {"create", "update", "delete"}:
        return _build_item_error(error_code="invalid_operation", entity_type=entity_type)

    entity_id = item.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        return _build_item_error(error_code="entity_id_required", entity_type=entity_type)

    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        return _build_item_error(error_code="payload_must_be_object", entity_type=entity_type)

    if payload.get("wallet_id") and payload.get("wallet_id") != wallet_id:
        return _build_item_error(error_code="wallet_id_mismatch", entity_type=entity_type)

    try:
        ensure_entity_id_matches(entity_id, payload.get("client_id"))
    except Exception:
        return _build_item_error(error_code="entity_id_mismatch", entity_type=entity_type)

    client_id = payload.get("client_id") or entity_id
    try:
        validate_client_id(client_id)
    except Exception:
        return _build_item_error(error_code="invalid_client_id", entity_type=entity_type, client_id=client_id)

    if operation in {"update", "delete"}:
        base_version = item.get("base_version")
        if base_version is None:
            return _build_item_error(
                error_code="base_version_required",
                entity_type=entity_type,
                client_id=client_id,
            )
        if not _is_number(base_version):
            return _build_item_error(
                error_code="base_version_invalid",
                entity_type=entity_type,
                client_id=client_id,
            )

    normalized = _apply_field_map(entity_type, payload)
    unknown_fields = _unknown_payload_fields(entity_type, normalized)
    if unknown_fields:
        return _build_item_error(
            error_code="invalid_field",
            entity_type=entity_type,
            client_id=client_id,
            detail=unknown_fields,
        )

    if operation == "create":
        required = SYNC_PUSH_REQUIRED_FIELDS_CREATE.get(entity_type, set())
        missing = [field for field in required if normalized.get(field) in (None, "")]
        if missing:
            return _build_item_error(
                error_code="missing_required_fields",
                entity_type=entity_type,
                client_id=client_id,
                detail=missing,
            )

        for group in SYNC_PUSH_REQUIRED_FIELD_GROUPS.get(entity_type, []):
            if not any(normalized.get(field) not in (None, "") for field in group):
                return _build_item_error(
                    error_code="missing_required_fields",
                    entity_type=entity_type,
                    client_id=client_id,
                    detail=sorted(group),
                )

        invalid_types = _invalid_field_types(normalized, required)
        if invalid_types:
            return _build_item_error(
                error_code="invalid_field_type",
                entity_type=entity_type,
                client_id=client_id,
                detail=invalid_types,
            )

    return None


def _prepare_doc_for_write(
    doctype: str,
    payload: Dict[str, Any],
    user: str,
    *,
    existing: Optional[frappe.model.document.Document],
) -> frappe.model.document.Document:
    doc = existing or frappe.new_doc(doctype)
    _set_owner(doc, user)

    client_id = payload.get("client_id")
    if not client_id:
        client_id = payload.get("entity_id")
    validate_client_id(client_id)
    doc.client_id = client_id

    if not existing:
        doc.name = client_id
        if doctype == "Hisabi Account":
            doc.flags.name_set = True
    elif doc.name != client_id:
        if doctype == "Hisabi Account":
            doc = _rename_doc_to_client_id(doc, client_id)
        else:
            frappe.throw(_("name must equal client_id"), frappe.ValidationError)

    payload = _apply_field_map(doctype, payload)
    payload = _strip_server_auth_fields(doctype, payload)
    payload = _filter_payload_fields(doc, payload)
    doc.update(payload)

    ensure_link_ownership(doctype, payload, user, wallet_id=getattr(doc, "wallet_id", None))

    return doc


def _rename_doc_to_client_id(
    doc: frappe.model.document.Document, client_id: str
) -> frappe.model.document.Document:
    if doc.name == client_id:
        return doc
    frappe.rename_doc(
        doc.doctype,
        doc.name,
        client_id,
        force=True,
        ignore_permissions=True,
    )
    return frappe.get_doc(doc.doctype, client_id)


def _to_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    dt = get_datetime(value)
    return dt.isoformat() if dt else None


def _minimal_server_record(doc: frappe.model.document.Document) -> Dict[str, Any]:
    return {
        "name": doc.name,
        "client_id": doc.client_id,
        "doc_version": doc.doc_version,
        "server_modified": _to_iso(doc.server_modified),
        "is_deleted": doc.is_deleted,
        "deleted_at": _to_iso(doc.deleted_at),
    }


def _sanitize_pull_record(doctype: str, record: Dict[str, Any]) -> Dict[str, Any]:
    if doctype == "Hisabi Device":
        record = dict(record)
        record.pop("device_token_hash", None)
    return record


def _conflict_response(doctype: str, doc: frappe.model.document.Document) -> Dict[str, Any]:
    return {
        "status": "conflict",
        "entity_type": doctype,
        "client_id": doc.client_id,
        "doc_version": doc.doc_version,
        "server_modified": _to_iso(doc.server_modified),
        "server_record": _minimal_server_record(doc),
    }

def _get_op_result(user: str, device_id: str, op_id: str) -> Optional[Dict[str, Any]]:
    if not device_id or not op_id:
        return None
    result_json = frappe.get_value("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": op_id}, "result_json")
    if not result_json:
        return None
    try:
        return json.loads(result_json)
    except json.JSONDecodeError:
        return None


def _write_audit_log(
    *,
    user: str,
    device_id: str,
    op_id: str,
    entity_type: str,
    entity_client_id: str,
    status: str,
    payload: Dict[str, Any],
) -> None:
    try:
        audit = frappe.new_doc("Hisabi Audit Log")
        audit.user = user
        audit.device_id = device_id
        audit.op_id = op_id
        audit.entity_type = entity_type
        audit.entity_client_id = entity_client_id
        audit.status = status
        audit.payload_json = json.dumps(payload, ensure_ascii=False)
        audit.save(ignore_permissions=True)
    except Exception:
        frappe.log_error("Failed to write audit log", "hisabi_backend.sync")


def _check_rate_limit(device_id: str) -> None:
    cache = frappe.cache()
    key = f"hisabi_sync_rate:{device_id}"
    max_requests = cint(frappe.conf.get("hisabi_sync_rate_limit_max", RATE_LIMIT_MAX))
    window_sec = cint(frappe.conf.get("hisabi_sync_rate_limit_window_sec", RATE_LIMIT_WINDOW_SEC))
    current = cint(cache.get_value(key) or 0)
    if current >= max_requests:
        frappe.throw(_("Rate limit exceeded"), frappe.PermissionError)
    cache.set_value(key, current + 1, expires_in_sec=window_sec)


@frappe.whitelist(allow_guest=False)
def sync_push(
    device_id: Optional[str] = None,
    wallet_id: Optional[str] = None,
    items: Optional[List[Dict[str, Any]] | str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Apply client changes to the server."""
    frappe.flags.disable_traceback = True
    request = getattr(frappe, "request", None)
    form_dict = getattr(frappe, "form_dict", {}) or {}
    json_body = None

    if request:
        try:
            json_body = request.get_json(silent=True)
        except Exception:
            json_body = None

    if device_id is None:
        device_id = form_dict.get("device_id")
    if wallet_id is None:
        wallet_id = form_dict.get("wallet_id")
    if items is None:
        items = form_dict.get("items")

    if request:
        if device_id is None:
            device_id = request.form.get("device_id") or request.args.get("device_id")
        if wallet_id is None:
            wallet_id = request.form.get("wallet_id") or request.args.get("wallet_id")
        if items is None:
            items = request.form.get("items") or request.args.get("items")

    if device_id is None and json_body:
        device_id = json_body.get("device_id")
    if wallet_id is None and json_body:
        wallet_id = json_body.get("wallet_id")
    if items is None and json_body:
        items = json_body.get("items")

    if items is None and request:
        raw = request.data.decode("utf-8") if request.data else ""
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                if device_id is None:
                    device_id = parsed.get("device_id")
                if wallet_id is None:
                    wallet_id = parsed.get("wallet_id")
                if items is None:
                    items = parsed.get("items")

    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            items = None

    if not device_id:
        return _build_sync_error("device_id_required", "device_id is required", status_code=417)
    if not wallet_id:
        return _build_sync_error("wallet_id_required", "wallet_id is required", status_code=417)
    if items is None:
        return _build_sync_error("items_required", "items is required", status_code=417)

    try:
        user, device = _require_device_auth(device_id)
        _check_rate_limit(device_id)
        wallet_id = validate_client_id(wallet_id)
    except Exception as exc:
        frappe.clear_last_message()
        return _build_sync_error(
            "auth_failed",
            str(exc) or "sync auth failed",
            status_code=_sync_status_for_exception(exc),
        )

    if not isinstance(items, list):
        return _build_sync_error("items_invalid", "items must be a list", status_code=417)

    if len(items) > MAX_PUSH_ITEMS:
        return _build_sync_response(
            {"message": {"results": [{"status": "error", "error": "too_many_items"}], "server_time": now_datetime().isoformat()}},
            status_code=417,
        )

    results: List[Dict[str, Any]] = []
    affected_accounts = set()
    affected_budgets = set()
    affected_goals = set()
    affected_debts = set()
    affected_jameyas = set()
    deleted_accounts = set()
    budgets_dirty = False
    goals_dirty = False

    for item in items:
        validation_error = _validate_sync_push_item(item, wallet_id)
        if validation_error and validation_error.get("error") in {"unsupported_entity_type", "doctype_not_installed"}:
            return _build_sync_response({"error": validation_error.get("error")}, status_code=417)

    # Wallet creation via sync: allow create for Hisabi Wallet even if user is not yet a member.
    # For all other mutations, require membership (viewer is read-only).
    member_info = None
    try:
        if any((i.get("entity_type") or "") != "Hisabi Wallet" for i in items):
            member_info = require_wallet_member(wallet_id, user, min_role="viewer")
    except Exception as exc:
        frappe.clear_last_message()
        return _build_sync_error(
            "wallet_access_denied",
            str(exc) or "wallet access denied",
            status_code=_sync_status_for_exception(exc),
        )

    if member_info and member_info.role == "viewer":
        # Viewer is read-only: block all mutations.
        for i in items:
            if (i.get("entity_type") or "") != "Hisabi Wallet Member":
                return _build_sync_error(
                    "wallet_read_only",
                    "Viewer role cannot sync_push mutations",
                    status_code=403,
                )

    for item in items:
        validation_error = _validate_sync_push_item(item, wallet_id)
        if validation_error:
            if validation_error.get("error") in {"unsupported_entity_type", "doctype_not_installed"}:
                return _build_sync_response({"error": validation_error.get("error")}, status_code=417)
            results.append(validation_error)
            continue

        entity_type = item.get("entity_type")
        op_id = item.get("op_id")
        operation = item.get("operation")
        payload = item.get("payload") or {}
        base_version = item.get("base_version")
        entity_id = item.get("entity_id")
        client_id = payload.get("client_id") or entity_id

        if op_id and _check_duplicate_op(user, device_id, op_id):
            stored = _get_op_result(user, device_id, op_id)
            if stored:
                results.append(stored)
            else:
                results.append(
                    {
                        "status": _get_op_status(user, device_id, op_id) or "accepted",
                        "entity_type": entity_type,
                        "client_id": client_id,
                    }
                )
            continue

        existing = _get_doc_by_client_id(entity_type, user, client_id, wallet_id=wallet_id)

        if operation == "create" and existing:
            doc = existing
            if entity_type == "Hisabi Account":
                doc = _prepare_doc_for_write(entity_type, payload, user, existing=existing)
                doc.save(ignore_permissions=True)
            result = {
                "status": "accepted",
                "entity_type": entity_type,
                "client_id": doc.client_id,
                "doc_version": doc.doc_version,
                "server_modified": _to_iso(doc.server_modified),
            }
            results.append(result)
            _store_op_id(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="duplicate",
                payload=item,
                result=result,
            )
            _write_audit_log(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="duplicate",
                payload=item,
            )
            continue

        if operation in {"update", "delete"} and not existing:
            result = _build_item_error(
                error_code="not_found",
                entity_type=entity_type,
                client_id=client_id,
            )
            results.append(result)
            _store_op_id(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="error",
                payload=item,
                result=result,
            )
            _write_audit_log(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="error",
                payload=item,
            )
            continue

        if existing and base_version is not None:
            if int(base_version) != int(existing.doc_version or 0):
                conflict = _conflict_response(entity_type, existing)
                results.append(conflict)
                _store_op_id(
                    user=user,
                    device_id=device_id,
                    op_id=op_id or "",
                    entity_type=entity_type,
                    entity_client_id=client_id,
                    status="conflict",
                    payload=item,
                    result=conflict,
                )
                _write_audit_log(
                    user=user,
                    device_id=device_id,
                    op_id=op_id or "",
                    entity_type=entity_type,
                    entity_client_id=client_id,
                    status="conflict",
                    payload=item,
                )
                continue

        payload_json = frappe.as_json(payload)
        if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            results.append(
                _build_item_error(
                    error_code="payload_too_large",
                    entity_type=entity_type,
                    client_id=client_id,
                )
            )
            continue

        # Enforce wallet scoping server-side.
        meta = frappe.get_meta(entity_type)
        if meta.has_field("wallet_id"):
            payload = dict(payload)
            payload["wallet_id"] = wallet_id

        # Wallet creation special case: create wallet + owner member.
        if entity_type == "Hisabi Wallet" and operation == "create":
            if client_id != wallet_id:
                results.append(
                    _build_item_error(
                        error_code="wallet_id_must_equal_client_id",
                        entity_type=entity_type,
                        client_id=client_id,
                    )
                )
                continue

        if entity_type != "Hisabi Wallet":
            # For all wallet-scoped entities, require at least member role.
            try:
                require_wallet_member(wallet_id, user, min_role="member")
            except Exception as exc:
                frappe.clear_last_message()
                results.append(
                    _build_item_error(
                        error_code="wallet_access_denied",
                        entity_type=entity_type,
                        client_id=client_id,
                        detail=str(exc) or "wallet access denied",
                    )
                )
                continue

        if entity_type in {"Hisabi Budget", "Hisabi Goal"}:
            payload = dict(payload)
            base_currency = _resolve_base_currency(wallet_id, user)
            if entity_type == "Hisabi Budget":
                if not payload.get("currency") and base_currency:
                    payload["currency"] = base_currency
                if payload.get("amount") is None and payload.get("amount_base") is not None:
                    payload["amount"] = payload.get("amount_base")
            if entity_type == "Hisabi Goal":
                if not payload.get("currency") and base_currency:
                    payload["currency"] = base_currency
                if payload.get("target_amount") is None and payload.get("target_amount_base") is not None:
                    payload["target_amount"] = payload.get("target_amount_base")

        doc = _prepare_doc_for_write(entity_type, payload, user, existing=existing)
        mark_deleted = operation == "delete"

        if entity_type == "Hisabi Account" and not existing:
            if not doc.current_balance and doc.opening_balance is not None:
                doc.current_balance = doc.opening_balance

        apply_common_sync_fields(doc, payload, bump_version=True, mark_deleted=mark_deleted)
        if entity_type == "Hisabi Account" and operation == "delete":
            doc.is_deleted = 1
            if not doc.deleted_at:
                doc.deleted_at = now_datetime()
        doc.save(ignore_permissions=True)
        if entity_type == "Hisabi Account" and doc.name != doc.client_id:
            doc = _rename_doc_to_client_id(doc, doc.client_id)
        if entity_type == "Hisabi Account" and operation == "delete":
            if not doc.deleted_at:
                doc.deleted_at = now_datetime()
            if doc.is_deleted != 1:
                doc.is_deleted = 1
            doc.db_set("is_deleted", 1, update_modified=False)
            doc.db_set("deleted_at", doc.deleted_at, update_modified=False)
            deleted_accounts.add(doc.name)

        if entity_type == "Hisabi Wallet" and operation == "create":
            # Ensure membership row exists for owner.
            if not frappe.db.exists("Hisabi Wallet Member", {"wallet": wallet_id, "user": user}):
                m = frappe.new_doc("Hisabi Wallet Member")
                m.wallet = wallet_id
                m.user = user
                m.role = "owner"
                m.status = "active"
                apply_common_sync_fields(m, bump_version=True, mark_deleted=False)
                m.save(ignore_permissions=True)

        if entity_type == "Hisabi Transaction":
            if doc.account:
                affected_accounts.add(doc.account)
            if doc.to_account:
                affected_accounts.add(doc.to_account)
            budgets_dirty = True
            goals_dirty = True

        if entity_type == "Hisabi Account":
            if operation == "update":
                affected_accounts.add(doc.name)
            goals_dirty = True

        if entity_type == "Hisabi Budget":
            affected_budgets.add(doc.name)

        if entity_type == "Hisabi Goal":
            affected_goals.add(doc.name)

        if entity_type == "Hisabi Debt":
            affected_debts.add(doc.name)
            goals_dirty = True

        if entity_type == "Hisabi Debt Installment":
            if doc.debt:
                affected_debts.add(doc.debt)
                goals_dirty = True

        if entity_type == "Hisabi Jameya":
            affected_jameyas.add(doc.name)

        if entity_type == "Hisabi Jameya Payment":
            if doc.jameya:
                affected_jameyas.add(doc.jameya)

        result = {
            "status": "accepted",
            "entity_type": entity_type,
            "client_id": doc.client_id,
            "doc_version": doc.doc_version,
            "server_modified": _to_iso(doc.server_modified),
        }
        results.append(result)

        _store_op_id(
            user=user,
            device_id=device_id,
            op_id=op_id or "",
            entity_type=entity_type,
            entity_client_id=doc.client_id,
            status="accepted",
            payload=item,
            result=result,
        )
        _write_audit_log(
            user=user,
            device_id=device_id,
            op_id=op_id or "",
            entity_type=entity_type,
            entity_client_id=doc.client_id,
            status="accepted",
            payload=item,
        )

    for account_name in affected_accounts:
        _recalculate_account_balance(user, account_name, wallet_id=wallet_id)

    if affected_debts:
        recalc_debts(user, affected_debts)

    if budgets_dirty and not affected_budgets:
        affected_budgets = {
            b.name for b in frappe.get_all("Hisabi Budget", filters={"wallet_id": wallet_id, "is_deleted": 0})
        }
    if affected_budgets:
        recalc_budgets(user, affected_budgets)

    if goals_dirty and not affected_goals:
        affected_goals = {g.name for g in frappe.get_all("Hisabi Goal", filters={"wallet_id": wallet_id, "is_deleted": 0})}
    if affected_goals:
        recalc_goals(user, affected_goals)

    if affected_jameyas:
        recalc_jameyas(user, affected_jameyas)

    for account_name in deleted_accounts:
        try:
            acc = frappe.get_doc("Hisabi Account", account_name)
        except Exception:
            continue
        if not acc.deleted_at:
            acc.deleted_at = now_datetime()
        if acc.is_deleted != 1:
            acc.is_deleted = 1
        acc.db_set("is_deleted", 1, update_modified=False)
        acc.db_set("deleted_at", acc.deleted_at, update_modified=False)

    device.last_sync_at = now_datetime()
    device.last_sync_ms = min(int(device.last_sync_at.timestamp() * 1000), 2147483647)
    device.save(ignore_permissions=True)

    return _build_sync_response(
        {"message": {"results": results, "server_time": now_datetime().isoformat()}},
        status_code=200,
    )


@frappe.whitelist(allow_guest=False)
def sync_pull(
    device_id: Optional[str] = None,
    wallet_id: Optional[str] = None,
    since: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Return server changes since cursor using server_modified."""

    def _coerce_json(value: Any) -> Any:
        if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
            return str(value)
        if isinstance(value, dict):
            return {str(k): _coerce_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_coerce_json(v) for v in value]
        return value

    def _parse_since_value(value: Any) -> Optional[datetime.datetime]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.lstrip("-").isdigit():
                try:
                    numeric = int(stripped)
                except ValueError:
                    return None
                seconds = numeric / 1000 if numeric >= 10**12 else numeric
                try:
                    return datetime.datetime.utcfromtimestamp(seconds)
                except (OverflowError, OSError, ValueError):
                    return None
        elif isinstance(value, (int, float)):
            numeric = int(value)
            seconds = numeric / 1000 if numeric >= 10**12 else numeric
            try:
                return datetime.datetime.utcfromtimestamp(seconds)
            except (OverflowError, OSError, ValueError):
                return None

        dt = get_datetime(value)
        return dt if dt else None

    from urllib.parse import parse_qs

    form_dict = frappe.form_dict or {}
    request = getattr(frappe.local, "request", None)
    json_body = None

    if device_id is None:
        device_id = form_dict.get("device_id")
    if wallet_id is None:
        wallet_id = form_dict.get("wallet_id")
    if since is None:
        since = form_dict.get("since")
    if cursor is None:
        cursor = form_dict.get("cursor")
    if limit is None:
        limit = form_dict.get("limit")

    if request and request.args:
        if device_id is None:
            device_id = request.args.get("device_id")
        if wallet_id is None:
            wallet_id = request.args.get("wallet_id")
        if since is None:
            since = request.args.get("since")
        if cursor is None:
            cursor = request.args.get("cursor")
        if limit is None:
            limit = request.args.get("limit")

    if request and getattr(request, "query_string", None):
        parsed = parse_qs(request.query_string.decode("utf-8"))
        if device_id is None:
            device_id = (parsed.get("device_id") or [None])[0]
        if wallet_id is None:
            wallet_id = (parsed.get("wallet_id") or [None])[0]
        if since is None:
            since = (parsed.get("since") or [None])[0]
        if cursor is None:
            cursor = (parsed.get("cursor") or [None])[0]
        if limit is None:
            limit = (parsed.get("limit") or [None])[0]

    if request and request.data and request.content_type and "application/json" in request.content_type:
        raw = request.data.decode("utf-8")
        if raw:
            try:
                json_body = json.loads(raw)
            except json.JSONDecodeError:
                json_body = None

    if isinstance(json_body, dict):
        if device_id is None:
            device_id = json_body.get("device_id")
        if wallet_id is None:
            wallet_id = json_body.get("wallet_id")
        if since is None:
            since = json_body.get("since")
        if cursor is None:
            cursor = json_body.get("cursor")
        if limit is None:
            limit = json_body.get("limit")

    if not device_id:
        return _build_sync_error("device_id_required", "device_id is required", status_code=417)
    if not wallet_id:
        return _build_sync_error("wallet_id_required", "wallet_id is required", status_code=417)

    try:
        user, device = _require_device_auth(device_id)
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="viewer")
    except Exception as exc:
        frappe.clear_last_message()
        return _build_sync_error(
            "auth_failed",
            str(exc) or "sync auth failed",
            status_code=_sync_status_for_exception(exc),
        )
    limit = min(int(limit or 500), 500)

    cursor_value = cursor or since
    since_dt = None
    if cursor_value:
        since_dt = _parse_since_value(cursor_value)
        if not since_dt:
            return _build_sync_error("invalid_cursor", "invalid_cursor", status_code=417)

    next_cursor = now_datetime()
    items: List[Dict[str, Any]] = []
    remaining = limit

    for doctype in DOCTYPE_LIST:
        if remaining <= 0:
            break
        if not frappe.db.exists("DocType", doctype):
            continue
        meta = frappe.get_meta(doctype)
        if not meta.has_field("server_modified"):
            continue

        filters: Dict[str, Any] = {}
        if doctype == "Hisabi Wallet":
            filters["name"] = wallet_id
        elif doctype == "Hisabi Wallet Member":
            filters["wallet"] = wallet_id
        elif meta.has_field("wallet_id"):
            filters["wallet_id"] = wallet_id
        elif meta.has_field("user"):
            filters["user"] = user
        else:
            filters["owner"] = user

        if since_dt:
            filters["server_modified"] = [">", since_dt]

        records = frappe.get_all(
            doctype,
            filters=filters,
            limit=remaining,
            order_by="server_modified asc",
            fields=["name", "server_modified"],
        )
        if not records:
            continue

        for row in records:
            doc = _sanitize_pull_record(doctype, frappe.get_doc(doctype, row.name).as_dict())
            doc = _coerce_json(doc)
            client_id = doc.get("client_id") or doc.get("name")
            item = {
                "entity_type": doctype,
                "entity_id": client_id,
                "client_id": client_id,
                "doc_version": doc.get("doc_version"),
                "server_modified": _to_iso(doc.get("server_modified")),
                "payload": doc,
                "is_deleted": doc.get("is_deleted"),
                "deleted_at": _to_iso(doc.get("deleted_at")),
            }
            items.append(item)
            remaining -= 1

            server_modified = doc.get("server_modified")
            if server_modified:
                server_modified_dt = get_datetime(server_modified)
                if server_modified_dt > next_cursor:
                    next_cursor = server_modified_dt

            if remaining <= 0:
                break

    device.last_pull_at = now_datetime()
    device.last_pull_ms = min(int(device.last_pull_at.timestamp() * 1000), 2147483647)
    device.db_set("last_pull_at", device.last_pull_at, update_modified=False)
    device.db_set("last_pull_ms", device.last_pull_ms, update_modified=False)

    return _build_sync_response(
        {"message": {"items": items, "next_cursor": next_cursor.isoformat(), "server_time": now_datetime().isoformat()}},
        status_code=200,
    )
