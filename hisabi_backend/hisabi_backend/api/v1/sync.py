"""Sync endpoints (v1)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime
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
    ensure_base_version,
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

RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW_SEC = 600
MAX_PUSH_ITEMS = 200
MAX_PAYLOAD_BYTES = 100 * 1024

SERVER_AUTH_FIELDS = {
    "Hisabi Account": {"current_balance"},
    "Hisabi Budget": {"spent_amount"},
    "Hisabi Goal": {"current_amount", "progress_percent", "remaining_amount"},
    "Hisabi Transaction": {"amount_base", "fx_rate_used"},
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
    elif doc.name != client_id:
        frappe.throw(_("name must equal client_id"), frappe.ValidationError)

    payload = _apply_field_map(doctype, payload)
    payload = _strip_server_auth_fields(doctype, payload)
    payload = _filter_payload_fields(doc, payload)
    doc.update(payload)

    ensure_link_ownership(doctype, payload, user, wallet_id=getattr(doc, "wallet_id", None))

    return doc


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
def sync_push(device_id: str, wallet_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply client changes to the server."""
    user, device = _require_device_auth(device_id)
    _check_rate_limit(device_id)
    wallet_id = validate_client_id(wallet_id)

    if not isinstance(items, list):
        frappe.throw(_("items must be a list"), frappe.ValidationError)

    if len(items) > MAX_PUSH_ITEMS:
        return {
            "results": [{"status": "error", "error": "too_many_items"}],
            "server_time": now_datetime().isoformat(),
        }

    results: List[Dict[str, Any]] = []
    affected_accounts = set()
    affected_budgets = set()
    affected_goals = set()
    affected_debts = set()
    affected_jameyas = set()
    budgets_dirty = False
    goals_dirty = False

    # Wallet creation via sync: allow create for Hisabi Wallet even if user is not yet a member.
    # For all other mutations, require membership (viewer is read-only).
    member_info = None
    if any((i.get("entity_type") or "") != "Hisabi Wallet" for i in items):
        member_info = require_wallet_member(wallet_id, user, min_role="viewer")

    if member_info and member_info.role == "viewer":
        # Viewer is read-only: block all mutations.
        for i in items:
            if (i.get("entity_type") or "") != "Hisabi Wallet Member":
                frappe.throw(_("Viewer role cannot sync_push mutations"), frappe.PermissionError)

    for item in items:
        entity_type = item.get("entity_type")
        op_id = item.get("op_id")
        operation = item.get("operation")
        payload = item.get("payload") or {}
        base_version = item.get("base_version")
        entity_id = item.get("entity_id")

        if not entity_type:
            results.append({"status": "error", "error": "entity_type required"})
            continue

        _ensure_supported_doctype(entity_type)

        if operation not in {"create", "update", "delete"}:
            results.append({"status": "error", "entity_type": entity_type, "error": "invalid operation"})
            continue

        ensure_entity_id_matches(entity_id, payload.get("client_id"))

        client_id = payload.get("client_id") or entity_id
        if not client_id:
            results.append({"status": "error", "entity_type": entity_type, "error": "client_id required"})
            continue

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

        if operation in {"update", "delete"} and not ensure_base_version(base_version):
            results.append(
                {
                    "status": "error",
                    "entity_type": entity_type,
                    "client_id": client_id,
                    "error": "base_version required",
                }
            )
            continue

        if operation == "create" and existing:
            result = {
                "status": "accepted",
                "entity_type": entity_type,
                "client_id": existing.client_id,
                "doc_version": existing.doc_version,
                "server_modified": _to_iso(existing.server_modified),
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
            result = {
                "status": "error",
                "entity_type": entity_type,
                "client_id": client_id,
                "error": "not_found",
            }
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
                {
                    "status": "error",
                    "entity_type": entity_type,
                    "client_id": client_id,
                    "error": "payload_too_large",
                }
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
                results.append({"status": "error", "entity_type": entity_type, "client_id": client_id, "error": "wallet_id must equal client_id"})
                continue

        if entity_type != "Hisabi Wallet":
            # For all wallet-scoped entities, require at least member role.
            require_wallet_member(wallet_id, user, min_role="member")

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
        doc.save(ignore_permissions=True)

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

    device.last_sync_at = now_datetime()
    device.last_sync_ms = int(device.last_sync_at.timestamp() * 1000)
    device.save(ignore_permissions=True)

    return {"results": results, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def sync_pull(device_id: str, wallet_id: str, cursor: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
    """Return server changes since cursor using server_modified."""
    user, device = _require_device_auth(device_id)
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")
    limit = min(int(limit or 500), 500)

    since = None
    if cursor:
        since = get_datetime(cursor)
        if not since:
            frappe.throw(_("Invalid cursor"), frappe.ValidationError)

    next_cursor = now_datetime()

    changes: Dict[str, List[Dict[str, Any]]] = {}
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

        if since:
            filters["server_modified"] = [">", since]

        records = frappe.get_all(
            doctype,
            filters=filters,
            limit=remaining,
            order_by="server_modified asc",
            fields=["name", "server_modified"],
        )
        if not records:
            continue

        docs = [
            _sanitize_pull_record(doctype, frappe.get_doc(doctype, row.name).as_dict()) for row in records
        ]
        changes[doctype] = docs
        remaining -= len(records)

        for doc in docs:
            server_modified = doc.get("server_modified")
            if server_modified:
                server_modified_dt = get_datetime(server_modified)
                if server_modified_dt > next_cursor:
                    next_cursor = server_modified_dt

    device.last_pull_at = now_datetime()
    device.last_pull_ms = int(device.last_pull_at.timestamp() * 1000)
    device.save(ignore_permissions=True)

    return {
        "changes": changes,
        "next_cursor": next_cursor.isoformat(),
        "server_time": now_datetime().isoformat(),
    }
