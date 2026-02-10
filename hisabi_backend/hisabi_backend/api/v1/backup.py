"""Backup export/restore APIs (Sprint 09)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from hisabi_backend.api.v1.sync import (
    SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES,
    SYNC_PULL_ALLOWED_FIELDS,
    SYNC_PUSH_ALLOWLIST,
)
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.wallet_acl import require_wallet_member

BACKUP_FORMAT = "hisabi_json_v1"

DOCTYPE_TO_ENTITY_KEY = {
    "Hisabi Wallet": "Wallet",
    "Hisabi Wallet Member": "WalletMember",
    "Hisabi Account": "Account",
    "Hisabi Category": "Category",
    "Hisabi Transaction": "Transaction",
    "Hisabi Bucket": "Bucket",
    "Hisabi Transaction Bucket": "TransactionBucket",
    "Hisabi Transaction Bucket Expense": "TransactionBucketExpense",
    "Hisabi Bucket Template": "BucketTemplate",
    "Hisabi Bucket Template Item": "BucketTemplateItem",
    "Hisabi Allocation Rule": "AllocationRule",
    "Hisabi Allocation Rule Line": "AllocationRuleLine",
    "Hisabi Transaction Allocation": "TransactionAllocation",
    "Hisabi Recurring Rule": "RecurringRule",
    "Hisabi Recurring Instance": "RecurringInstance",
    "Hisabi Budget": "Budget",
    "Hisabi Goal": "Goal",
    "Hisabi Debt": "Debt",
    "Hisabi Debt Installment": "DebtInstallment",
    "Hisabi Debt Request": "DebtRequest",
    "Hisabi Jameya": "Jameya",
    "Hisabi Jameya Payment": "JameyaPayment",
    "Hisabi Attachment": "Attachment",
}
ENTITY_KEY_TO_DOCTYPE = {value: key for key, value in DOCTYPE_TO_ENTITY_KEY.items()}

EXPORT_DOCTYPES = list(SYNC_PUSH_ALLOWLIST)

# Minimal restore graph checks for critical integrity.
REFERENCE_RULES = {
    "Transaction": [("account", "Account"), ("to_account", "Account"), ("category", "Category"), ("bucket", "Bucket")],
    "TransactionBucket": [("transaction", "Transaction"), ("bucket", "Bucket")],
    "TransactionBucketExpense": [("transaction_id", "Transaction"), ("bucket_id", "Bucket")],
    "AllocationRuleLine": [("rule", "AllocationRule"), ("bucket", "Bucket")],
    "TransactionAllocation": [("transaction", "Transaction"), ("bucket", "Bucket")],
    "RecurringRule": [("account_id", "Account"), ("category_id", "Category")],
    "RecurringInstance": [("rule_id", "RecurringRule"), ("transaction_id", "Transaction")],
    "DebtInstallment": [("debt", "Debt")],
    "JameyaPayment": [("jameya", "Jameya")],
    "Attachment": [("owner_client_id", "Transaction")],
    "BucketTemplateItem": [("bucket_id", "Bucket")],
}

REQUIRED_FIELDS = {
    "Wallet": ["client_id"],
    "Account": ["client_id"],
    "Category": ["client_id"],
    "Transaction": ["client_id", "transaction_type", "amount", "currency"],
    "Bucket": ["client_id"],
    "BucketTemplate": ["client_id", "title"],
    "RecurringRule": ["client_id", "title", "transaction_type", "amount"],
    "RecurringInstance": ["client_id", "rule_id", "occurrence_date"],
}


@dataclass
class ValidationResult:
    counts: Dict[str, int]
    warnings: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]


def _set_http_status(code: int) -> None:
    frappe.local.response["http_status_code"] = code


def _error_response(code: str, message: str, *, details: Optional[List[Dict[str, Any]]] = None, status: int = 422) -> Dict[str, Any]:
    _set_http_status(status)
    payload: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


def _repo_commit() -> str:
    try:
        root = Path(__file__).resolve().parents[4]
        return (
            subprocess.check_output(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], text=True)
            .strip()
        )
    except Exception:
        return "unknown"


def _app_version() -> str:
    try:
        return version("hisabi_backend")
    except PackageNotFoundError:
        return "unknown"


def _wallet_scoped_filters(doctype: str, wallet_id: str) -> Dict[str, Any]:
    meta = frappe.get_meta(doctype)
    if doctype == "Hisabi Wallet":
        return {"name": wallet_id}
    if meta.has_field("wallet_id"):
        return {"wallet_id": wallet_id}
    if meta.has_field("wallet"):
        return {"wallet": wallet_id}
    return {"name": "__no_match__"}


def _entity_key_for_doctype(doctype: str) -> str:
    return DOCTYPE_TO_ENTITY_KEY.get(doctype) or doctype.replace("Hisabi ", "").replace(" ", "")


def _doctype_for_entity_key(entity_key: str) -> Optional[str]:
    if entity_key in ENTITY_KEY_TO_DOCTYPE:
        return ENTITY_KEY_TO_DOCTYPE[entity_key]
    if entity_key.startswith("Hisabi "):
        return entity_key
    return None


def _allowed_export_fields(doctype: str) -> List[str]:
    allowed = set(SYNC_PULL_ALLOWED_FIELDS.get(doctype, set()))
    allowed |= {
        "name",
        "client_id",
        "wallet_id",
        "wallet",
        "doc_version",
        "server_modified",
        "is_deleted",
        "deleted_at",
        "base_version",
    }
    meta = frappe.get_meta(doctype)
    filtered: List[str] = []
    for field in sorted(allowed):
        if field == "name":
            filtered.append(field)
            continue
        if not meta.has_field(field):
            continue
        df = meta.get_field(field)
        if df and df.fieldtype in {"Table", "Table MultiSelect", "Section Break", "Column Break", "Tab Break"}:
            continue
        filtered.append(field)
    return filtered


def _fetch_wallet_rows(doctype: str, wallet_id: str) -> List[Dict[str, Any]]:
    filters = _wallet_scoped_filters(doctype, wallet_id)
    fields = _allowed_export_fields(doctype)
    rows = frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)
    if doctype in SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES:
        for row in rows:
            if not row.get("client_id") and row.get("name"):
                row["client_id"] = row.get("name")
    return rows


def _extract_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _arg_value(name: str, value: Any = None) -> Any:
    if value not in (None, ""):
        return value
    form_value = frappe.form_dict.get(name)
    if form_value not in (None, ""):
        return form_value
    req = getattr(frappe.local, "request", None)
    if req:
        args_value = req.args.get(name)
        if args_value not in (None, ""):
            return args_value
        form_post_value = req.form.get(name)
        if form_post_value not in (None, ""):
            return form_post_value
        try:
            body_json = req.get_json(silent=True)
            if isinstance(body_json, dict) and body_json.get(name) not in (None, ""):
                return body_json.get(name)
        except Exception:
            pass
    return value


def _normalize_entities(payload: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    warnings: List[Dict[str, Any]] = []
    entities_raw = payload.get("entities")
    if not isinstance(entities_raw, dict):
        return {}, warnings

    entities: Dict[str, List[Dict[str, Any]]] = {}
    for key, value in entities_raw.items():
        if not isinstance(value, list):
            warnings.append({"code": "invalid_entity_array", "entity": key, "message": "entity payload is not an array"})
            continue
        rows = [row for row in value if isinstance(row, dict)]
        entities[key] = rows
    return entities, warnings


def _record_id(row: Dict[str, Any]) -> Optional[str]:
    return row.get("client_id") or row.get("name")


def _row_wallet_id(row: Dict[str, Any]) -> Optional[str]:
    return row.get("wallet_id") or row.get("wallet")


def _validate_restore_payload(*, wallet_id: str, payload: Dict[str, Any], check_collisions: bool = True) -> ValidationResult:
    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    fmt = payload.get("meta", {}).get("format") if isinstance(payload.get("meta"), dict) else None
    if fmt and fmt != BACKUP_FORMAT:
        warnings.append({"code": "unknown_format", "message": f"expected {BACKUP_FORMAT}, got {fmt}"})

    entities, normalize_warnings = _normalize_entities(payload)
    warnings.extend(normalize_warnings)

    counts = {key: len(rows) for key, rows in entities.items()}
    present_entity_keys = set(entities.keys())

    for key, rows in entities.items():
        for idx, row in enumerate(rows):
            row_id = _record_id(row)
            row_wallet = _row_wallet_id(row)
            if row_wallet and row_wallet != wallet_id:
                errors.append(
                    {
                        "code": "wallet_mismatch",
                        "entity": key,
                        "index": idx,
                        "record_id": row_id,
                        "message": f"row wallet_id={row_wallet} does not match target wallet_id={wallet_id}",
                    }
                )

            req = REQUIRED_FIELDS.get(key, [])
            for field in req:
                if row.get(field) in (None, ""):
                    errors.append(
                        {
                            "code": "missing_required_fields",
                            "entity": key,
                            "index": idx,
                            "record_id": row_id,
                            "field": field,
                            "message": f"missing required field: {field}",
                        }
                    )

            if check_collisions and row_id:
                doctype = _doctype_for_entity_key(key)
                if doctype and frappe.db.exists(doctype, row_id):
                    meta = frappe.get_meta(doctype)
                    existing_wallet = None
                    if meta.has_field("wallet_id"):
                        existing_wallet = frappe.db.get_value(doctype, row_id, "wallet_id")
                    elif meta.has_field("wallet"):
                        existing_wallet = frappe.db.get_value(doctype, row_id, "wallet")
                    if existing_wallet and existing_wallet != wallet_id:
                        errors.append(
                            {
                                "code": "duplicate_id_collision",
                                "entity": key,
                                "index": idx,
                                "record_id": row_id,
                                "message": f"record {row_id} already belongs to wallet {existing_wallet}",
                            }
                        )

    entity_ids = {
        key: {record_id for record in rows if (record_id := _record_id(record))}
        for key, rows in entities.items()
    }

    for entity_key, refs in REFERENCE_RULES.items():
        if entity_key not in present_entity_keys:
            continue
        rows = entities.get(entity_key, [])
        for idx, row in enumerate(rows):
            if cint(row.get("is_deleted")):
                continue
            for field_name, target_entity in refs:
                ref_value = row.get(field_name)
                if not ref_value:
                    continue
                target_ids = entity_ids.get(target_entity, set())
                if ref_value not in target_ids and ref_value != wallet_id:
                    errors.append(
                        {
                            "code": "invalid_reference",
                            "entity": entity_key,
                            "index": idx,
                            "record_id": _record_id(row),
                            "field": field_name,
                            "target_entity": target_entity,
                            "target_id": ref_value,
                            "message": f"{entity_key}.{field_name} references missing {target_entity} {ref_value}",
                        }
                    )

    known_keys = set(DOCTYPE_TO_ENTITY_KEY.values())
    for key in present_entity_keys:
        if key not in known_keys:
            warnings.append({"code": "unknown_entity_type", "entity": key, "message": "entity will be ignored"})

    return ValidationResult(counts=counts, warnings=warnings, errors=errors)


def _coerce_field_payload(doctype: str, row: Dict[str, Any], wallet_id: str) -> Dict[str, Any]:
    allowed = set(_allowed_export_fields(doctype))
    payload = {key: value for key, value in row.items() if key in allowed}
    payload["wallet_id"] = wallet_id
    if "client_id" not in payload and row.get("name"):
        payload["client_id"] = row.get("name")
    return payload


def _upsert_row(doctype: str, row: Dict[str, Any], wallet_id: str, user: str) -> str:
    row_id = _record_id(row)
    if not row_id:
        raise frappe.ValidationError(_("record id missing"))

    payload = _coerce_field_payload(doctype, row, wallet_id)
    doc = None

    if frappe.db.exists(doctype, row_id):
        doc = frappe.get_doc(doctype, row_id)
    elif frappe.get_meta(doctype).has_field("client_id"):
        existing_name = frappe.db.get_value(doctype, {"client_id": row_id}, "name")
        if existing_name:
            doc = frappe.get_doc(doctype, existing_name)

    if doc is None:
        doc = frappe.new_doc(doctype)
        if doctype in SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES:
            doc.name = row_id
        if doc.meta.has_field("client_id"):
            doc.client_id = row_id

    # Wallet ownership must be enforced and immutable across restore.
    if doc.meta.has_field("wallet_id"):
        doc.wallet_id = wallet_id
    if doc.meta.has_field("wallet"):
        doc.wallet = wallet_id
    if doc.meta.has_field("user") and not doc.get("user"):
        doc.user = user

    for key, value in payload.items():
        if key in {"name", "wallet", "wallet_id"}:
            continue
        if doc.meta.has_field(key):
            doc.set(key, value)

    doc.flags.ignore_links = True
    doc.save(ignore_permissions=True)
    return doc.name


@frappe.whitelist(allow_guest=False)
def export(wallet_id: Optional[str] = None, format: str = BACKUP_FORMAT) -> Dict[str, Any]:
    wallet_id = _arg_value("wallet_id", wallet_id)
    format = _arg_value("format", format)
    if not wallet_id:
        return _error_response("wallet_id_required", "wallet_id is required", status=400)
    if format != BACKUP_FORMAT:
        return _error_response("unsupported_format", f"unsupported format: {format}", status=422)

    user, _device = require_device_token_auth()
    require_wallet_member(wallet_id, user)

    entities: Dict[str, List[Dict[str, Any]]] = {}
    for doctype in EXPORT_DOCTYPES:
        entity_key = _entity_key_for_doctype(doctype)
        entities[entity_key] = _fetch_wallet_rows(doctype, wallet_id)

    template_ids = [row.get("name") for row in entities.get("BucketTemplate", []) if row.get("name")]
    if template_ids:
        entities["BucketTemplateItem"] = frappe.get_all(
            "Hisabi Bucket Template Item",
            filters={"parent": ["in", template_ids], "parenttype": "Hisabi Bucket Template"},
            fields=["name", "parent", "bucket_id", "percentage", "idx"],
            limit_page_length=0,
        )
    else:
        entities["BucketTemplateItem"] = []

    return {
        "meta": {
            "format": BACKUP_FORMAT,
            "exported_at": now_datetime().isoformat(),
            "app_version": _app_version(),
            "commit": _repo_commit(),
            "wallet_id": wallet_id,
        },
        "entities": entities,
    }


@frappe.whitelist(allow_guest=False)
def validate_restore(wallet_id: Optional[str] = None, payload: Any = None) -> Dict[str, Any]:
    wallet_id = _arg_value("wallet_id", wallet_id)
    payload = _arg_value("payload", payload)
    if not wallet_id:
        return _error_response("wallet_id_required", "wallet_id is required", status=400)

    user, _device = require_device_token_auth()
    require_wallet_member(wallet_id, user)

    parsed = _extract_payload(payload)
    result = _validate_restore_payload(wallet_id=wallet_id, payload=parsed, check_collisions=True)
    response: Dict[str, Any] = {
        "counts": result.counts,
        "warnings": result.warnings,
        "status": "ok" if not result.errors else "error",
    }
    if result.errors:
        error_payload = _error_response(
            "restore_validation_failed",
            "restore payload has critical issues",
            details=result.errors,
            status=422,
        )
        error_payload["counts"] = result.counts
        error_payload["warnings"] = result.warnings
        return error_payload
    return response


@frappe.whitelist(allow_guest=False)
def apply_restore(wallet_id: Optional[str] = None, payload: Any = None, mode: str = "merge") -> Dict[str, Any]:
    wallet_id = _arg_value("wallet_id", wallet_id)
    payload = _arg_value("payload", payload)
    mode = _arg_value("mode", mode) or "merge"
    if not wallet_id:
        return _error_response("wallet_id_required", "wallet_id is required", status=400)
    if mode != "merge":
        return _error_response("unsupported_mode", f"unsupported restore mode: {mode}", status=422)

    user, _device = require_device_token_auth()
    require_wallet_member(wallet_id, user)

    parsed = _extract_payload(payload)
    validation = _validate_restore_payload(wallet_id=wallet_id, payload=parsed, check_collisions=True)
    if validation.errors:
        error_payload = _error_response(
            "restore_validation_failed",
            "restore payload has critical issues",
            details=validation.errors,
            status=422,
        )
        error_payload["counts"] = validation.counts
        error_payload["warnings"] = validation.warnings
        return error_payload

    entities, _warnings = _normalize_entities(parsed)
    applied_counts: Dict[str, int] = {}

    try:
        for entity_key, rows in entities.items():
            doctype = _doctype_for_entity_key(entity_key)
            if not doctype or doctype == "Hisabi Bucket Template Item":
                continue
            applied = 0
            for row in rows:
                _upsert_row(doctype, row, wallet_id, user)
                applied += 1
            if applied:
                applied_counts[entity_key] = applied

        # Child-table restore: merge template items after parent upserts.
        for item in entities.get("BucketTemplateItem", []):
            parent = item.get("parent")
            if not parent or not frappe.db.exists("Hisabi Bucket Template", parent):
                continue
            template = frappe.get_doc("Hisabi Bucket Template", parent)
            rows = list(getattr(template, "template_items", []) or [])
            existing = next((row for row in rows if row.bucket_id == item.get("bucket_id")), None)
            if existing:
                existing.percentage = item.get("percentage")
            else:
                template.append(
                    "template_items",
                    {
                        "bucket_id": item.get("bucket_id"),
                        "percentage": item.get("percentage"),
                    },
                )
            template.save(ignore_permissions=True)
            applied_counts["BucketTemplateItem"] = applied_counts.get("BucketTemplateItem", 0) + 1

        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise

    return {
        "status": "ok",
        "mode": mode,
        "counts": validation.counts,
        "applied": applied_counts,
        "warnings": validation.warnings,
    }
