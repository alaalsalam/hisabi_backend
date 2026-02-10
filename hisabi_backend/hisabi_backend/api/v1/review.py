"""Review Center APIs (Sprint 11)."""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict, Optional

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime
from werkzeug.wrappers import Response

from hisabi_backend.domain.allocation_engine import set_manual_allocations as set_manual_allocations_engine
from hisabi_backend.utils.bucket_allocations import (
    InvalidBucketAllocationError,
    InvalidBucketExpenseAssignmentError,
    ensure_bucket_wallet_scope,
    ensure_expense_transaction,
    ensure_income_transaction,
)
from hisabi_backend.utils.request_params import get_request_param
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member

from . import reports_finance

TX_DTYPE = "Hisabi Transaction"
TX_BUCKET_DTYPE = "Hisabi Transaction Bucket"
TX_ALLOC_DTYPE = "Hisabi Transaction Allocation"
TX_EXPENSE_BUCKET_DTYPE = "Hisabi Transaction Bucket Expense"
RULE_DTYPE = "Hisabi Recurring Rule"
INSTANCE_DTYPE = "Hisabi Recurring Instance"
AUDIT_DTYPE = "Hisabi Audit Log"

SUPPORTED_ISSUE_TYPES = {
    "missing_income_allocation",
    "missing_expense_bucket",
    "fx_missing",
    "orphan_recurring_instance",
    "duplicate_recurring_output",
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> Response:
    response = Response()
    response.mimetype = "application/json"
    response.status_code = status_code
    response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return response


def _build_invalid_request(message: str, *, param: Optional[str] = None, detail: Any = None) -> Response:
    payload: Dict[str, Any] = {"error": {"code": "invalid_request", "message": message}}
    if param:
        payload["error"]["param"] = param
    if detail is not None:
        payload["error"]["detail"] = detail
    return _json_response(payload, status_code=422)


def _request_payload() -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    form_dict = getattr(frappe, "form_dict", {}) or {}
    if isinstance(form_dict, dict):
        payload.update(form_dict)
    request = getattr(frappe, "request", None)
    if request:
        try:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                payload.update(body)
        except Exception:
            pass
        try:
            raw = request.data.decode("utf-8") if request.data else ""
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload.update(parsed)
        except Exception:
            pass
    return payload


def _resolve_param(value: Any, name: str) -> Any:
    if value not in (None, ""):
        return value
    return get_request_param(name)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n", ""}:
            return False
    return False


def _parse_date(value: Any, *, param: str) -> datetime.date:
    parsed = get_datetime(value)
    if not parsed:
        raise ValueError(f"{param} is invalid")
    return parsed.date()


def _resolve_window(from_date: Any, to_date: Any) -> tuple[datetime.date, datetime.date]:
    today = now_datetime().date()
    resolved_from = _parse_date(from_date, param="from_date") if from_date else today - datetime.timedelta(days=30)
    resolved_to = _parse_date(to_date, param="to_date") if to_date else today
    if resolved_to < resolved_from:
        raise ValueError("to_date must be greater than or equal to from_date")
    return resolved_from, resolved_to


def _iso(value: Any) -> Optional[str]:
    if not value:
        return None
    parsed = get_datetime(value)
    return parsed.isoformat() if parsed else None


def _coerce_date_iso(value: Any) -> Optional[str]:
    parsed = get_datetime(value)
    if not parsed:
        return None
    return parsed.date().isoformat()


def _issue_id(issue_type: str, wallet_id: str, primary_entity_id: str, date_hint: Optional[str] = None) -> str:
    raw = f"{issue_type}|{wallet_id}|{primary_entity_id}|{date_hint or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"ISSUE-{digest}"


def _app_version() -> str:
    try:
        return version("hisabi_backend")
    except PackageNotFoundError:
        return "unknown"


def _repo_commit() -> str:
    try:
        root = Path(__file__).resolve().parents[4]
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _active_bucket_ids(wallet_id: str) -> list[str]:
    rows = frappe.get_all(
        "Hisabi Bucket",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "is_active", "archived"],
        order_by="creation asc",
    )
    active: list[str] = []
    for row in rows:
        is_active = row.get("is_active")
        if is_active in (None, ""):
            is_active = 0 if cint(row.get("archived") or 0) else 1
        if cint(is_active):
            active.append(row.get("name"))
    return [bucket_id for bucket_id in active if bucket_id]


def _query_transactions(wallet_id: str, from_date: datetime.date, to_date: datetime.date) -> list[Dict[str, Any]]:
    from_dt = datetime.datetime.combine(from_date, datetime.time.min)
    to_dt_exclusive = datetime.datetime.combine(to_date + datetime.timedelta(days=1), datetime.time.min)
    return frappe.db.sql(
        """
        SELECT
            name,
            client_id,
            transaction_type,
            amount,
            amount_base,
            currency,
            account,
            to_account,
            category,
            bucket,
            date_time,
            creation
        FROM `tabHisabi Transaction`
        WHERE wallet_id=%s
          AND is_deleted=0
          AND date_time >= %s
          AND date_time < %s
        ORDER BY date_time ASC, name ASC
        """,
        (wallet_id, from_dt, to_dt_exclusive),
        as_dict=True,
    )


def _query_income_allocation_map(wallet_id: str, tx_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    state: Dict[str, Dict[str, Any]] = {}
    if not tx_ids:
        return state

    if frappe.db.exists("DocType", TX_BUCKET_DTYPE):
        rows = frappe.db.sql(
            f"""
            SELECT transaction_id, amount
            FROM `tab{TX_BUCKET_DTYPE}`
            WHERE wallet_id=%(wallet_id)s
              AND is_deleted=0
              AND transaction_id IN %(tx_ids)s
            """,
            {"wallet_id": wallet_id, "tx_ids": tuple(tx_ids)},
            as_dict=True,
        )
        for row in rows:
            tx_id = row.get("transaction_id")
            if not tx_id:
                continue
            current = state.setdefault(tx_id, {"count": 0, "sum": 0.0, "source": "canonical"})
            current["count"] = int(current.get("count") or 0) + 1
            current["sum"] = flt(current.get("sum") or 0) + flt(row.get("amount") or 0)

    if frappe.db.exists("DocType", TX_ALLOC_DTYPE):
        rows = frappe.db.sql(
            f"""
            SELECT transaction, amount
            FROM `tab{TX_ALLOC_DTYPE}`
            WHERE wallet_id=%(wallet_id)s
              AND is_deleted=0
              AND transaction IN %(tx_ids)s
            """,
            {"wallet_id": wallet_id, "tx_ids": tuple(tx_ids)},
            as_dict=True,
        )
        for row in rows:
            tx_id = row.get("transaction")
            if not tx_id:
                continue
            current = state.setdefault(tx_id, {"count": 0, "sum": 0.0, "source": "legacy"})
            if current.get("source") == "canonical":
                # Canonical rows have precedence when both mirrors exist.
                continue
            current["count"] = int(current.get("count") or 0) + 1
            current["sum"] = flt(current.get("sum") or 0) + flt(row.get("amount") or 0)
    return state


def _query_expense_assignment_set(wallet_id: str, tx_ids: list[str]) -> set[str]:
    if not tx_ids:
        return set()
    if not frappe.db.exists("DocType", TX_EXPENSE_BUCKET_DTYPE):
        return set()
    rows = frappe.db.sql(
        f"""
        SELECT transaction_id
        FROM `tab{TX_EXPENSE_BUCKET_DTYPE}`
        WHERE wallet_id=%(wallet_id)s
          AND is_deleted=0
          AND transaction_id IN %(tx_ids)s
        """,
        {"wallet_id": wallet_id, "tx_ids": tuple(tx_ids)},
        as_dict=True,
    )
    return {row.get("transaction_id") for row in rows if row.get("transaction_id")}


def _query_instances(wallet_id: str, from_date: datetime.date, to_date: datetime.date) -> list[Dict[str, Any]]:
    return frappe.get_all(
        INSTANCE_DTYPE,
        filters={
            "wallet_id": wallet_id,
            "is_deleted": 0,
            "occurrence_date": ["between", [from_date, to_date]],
        },
        fields=[
            "name",
            "client_id",
            "rule_id",
            "occurrence_date",
            "transaction_id",
            "status",
            "skip_reason",
            "generated_at",
            "creation",
            "doc_version",
            "server_modified",
        ],
        order_by="occurrence_date asc, creation asc, name asc",
    )


def _query_active_transactions_map(wallet_id: str, tx_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    if not tx_ids:
        return {}
    rows = frappe.db.sql(
        """
        SELECT name, client_id, date_time, creation, is_deleted
        FROM `tabHisabi Transaction`
        WHERE wallet_id=%(wallet_id)s
          AND name IN %(tx_ids)s
        """,
        {"wallet_id": wallet_id, "tx_ids": tuple(tx_ids)},
        as_dict=True,
    )
    return {row.get("name"): row for row in rows if row.get("name") and cint(row.get("is_deleted") or 0) == 0}


def _load_fx_ack_issue_ids(wallet_id: str) -> set[str]:
    rows = frappe.get_all(
        AUDIT_DTYPE,
        filters={
            "wallet_id": wallet_id,
            "event_type": "review.fx_missing_ack",
            "status": "accepted",
        },
        fields=["entity_client_id"],
        limit_page_length=2000,
    )
    return {row.get("entity_client_id") for row in rows if row.get("entity_client_id")}


def _build_issue(
    *,
    issue_type: str,
    severity: str,
    wallet_id: str,
    primary_entity_id: str,
    date_hint: Optional[str],
    entity: Dict[str, Any],
    message: str,
    details: Dict[str, Any],
    suggested_actions: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "issue_id": _issue_id(issue_type, wallet_id, primary_entity_id, date_hint),
        "type": issue_type,
        "severity": severity,
        "entity": entity,
        "message": message,
        "details": details,
        "suggested_actions": suggested_actions,
    }


def _collect_review_issues(
    *,
    wallet_id: str,
    user: str,
    from_date: datetime.date,
    to_date: datetime.date,
    include_resolved: bool,
) -> list[Dict[str, Any]]:
    issues: list[Dict[str, Any]] = []

    tx_rows = _query_transactions(wallet_id, from_date, to_date)
    tx_by_id = {row.get("name"): row for row in tx_rows if row.get("name")}
    tx_ids = [row_id for row_id in tx_by_id]

    income_tx_ids = [
        row.get("name")
        for row in tx_rows
        if (row.get("transaction_type") or "").strip().lower() == "income" and row.get("name")
    ]
    income_alloc_state = _query_income_allocation_map(wallet_id, income_tx_ids)

    for tx_id in income_tx_ids:
        tx = tx_by_id.get(tx_id) or {}
        state = income_alloc_state.get(tx_id) or {"count": 0, "sum": 0.0, "source": "none"}
        alloc_count = cint(state.get("count") or 0)
        alloc_sum = flt(state.get("sum") or 0)
        tx_amount = flt(tx.get("amount") or 0)
        missing = alloc_count <= 0
        mismatch = alloc_count > 0 and abs(alloc_sum - tx_amount) > 0.01
        if not missing and not mismatch:
            continue

        message = "Income transaction is missing bucket allocations" if missing else "Income allocations do not match transaction amount"
        issues.append(
            _build_issue(
                issue_type="missing_income_allocation",
                severity="high",
                wallet_id=wallet_id,
                primary_entity_id=tx_id,
                date_hint=_coerce_date_iso(tx.get("date_time")),
                entity={
                    "doctype": TX_DTYPE,
                    "id": tx_id,
                    "date": _coerce_date_iso(tx.get("date_time")),
                    "transaction_id": tx_id,
                },
                message=message,
                details={
                    "expected_amount": tx_amount,
                    "allocated_amount": alloc_sum,
                    "allocation_rows": alloc_count,
                    "allocation_source": state.get("source") or "none",
                    "currency": tx.get("currency"),
                },
                suggested_actions=[
                    {
                        "action": "open_allocation",
                        "payload": {
                            "transaction_id": tx_id,
                            "amount": tx_amount,
                            "currency": tx.get("currency"),
                        },
                    }
                ],
            )
        )

    expense_assignment_set = _query_expense_assignment_set(wallet_id, tx_ids)
    for tx in tx_rows:
        tx_type = (tx.get("transaction_type") or "").strip().lower()
        tx_id = tx.get("name")
        if tx_type != "expense" or not tx_id:
            continue
        if tx.get("bucket"):
            continue
        if tx_id in expense_assignment_set:
            continue
        issues.append(
            _build_issue(
                issue_type="missing_expense_bucket",
                severity="medium",
                wallet_id=wallet_id,
                primary_entity_id=tx_id,
                date_hint=_coerce_date_iso(tx.get("date_time")),
                entity={
                    "doctype": TX_DTYPE,
                    "id": tx_id,
                    "date": _coerce_date_iso(tx.get("date_time")),
                    "transaction_id": tx_id,
                },
                message="Expense transaction is missing bucket assignment",
                details={
                    "amount": flt(tx.get("amount") or 0),
                    "currency": tx.get("currency"),
                },
                suggested_actions=[
                    {
                        "action": "assign_bucket",
                        "payload": {
                            "transaction_id": tx_id,
                            "amount": flt(tx.get("amount") or 0),
                            "currency": tx.get("currency"),
                        },
                    }
                ],
            )
        )

    warnings: list[Dict[str, Any]] = []
    warning_seen: set[str] = set()
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    base_currency = reports_finance._resolve_wallet_base_currency(wallet_id, user)
    for tx in tx_rows:
        reports_finance._tx_amount_in_base(
            tx=tx,
            wallet_id=wallet_id,
            base_currency=base_currency,
            fx_cache=fx_cache,
            warnings=warnings,
            warning_seen=warning_seen,
        )

    fx_ack_ids = _load_fx_ack_issue_ids(wallet_id)
    for warning in warnings:
        tx_id = warning.get("tx_id")
        if not tx_id:
            continue
        issue = _build_issue(
            issue_type="fx_missing",
            severity="low",
            wallet_id=wallet_id,
            primary_entity_id=str(tx_id),
            date_hint=_coerce_date_iso(warning.get("date_time")),
            entity={
                "doctype": TX_DTYPE,
                "id": tx_id,
                "date": _coerce_date_iso(warning.get("date_time")),
                "transaction_id": tx_id,
            },
            message="FX rate is missing for report conversion",
            details={
                "currency": warning.get("currency"),
                "base_currency": warning.get("base_currency") or base_currency,
                "date_time": warning.get("date_time"),
            },
            suggested_actions=[
                {
                    "action": "add_fx_rate",
                    "payload": {
                        "transaction_id": tx_id,
                        "currency": warning.get("currency"),
                        "base_currency": warning.get("base_currency") or base_currency,
                        "date": _coerce_date_iso(warning.get("date_time")),
                        "acknowledge": True,
                    },
                }
            ],
        )
        issue_id = issue.get("issue_id")
        acknowledged = issue_id in fx_ack_ids
        if acknowledged and not include_resolved:
            continue
        if acknowledged:
            issue["details"] = {**(issue.get("details") or {}), "acknowledged": True}
        issues.append(issue)

    instances = _query_instances(wallet_id, from_date, to_date)
    instance_tx_ids = sorted({row.get("transaction_id") for row in instances if row.get("transaction_id")})
    active_tx_map = _query_active_transactions_map(wallet_id, instance_tx_ids)
    today = now_datetime().date()

    for row in instances:
        instance_id = row.get("name")
        if not instance_id:
            continue
        occ_iso = _coerce_date_iso(row.get("occurrence_date"))
        status = (row.get("status") or "").strip().lower() or "scheduled"
        tx_id = (row.get("transaction_id") or "").strip()
        is_past = bool(occ_iso and occ_iso < today.isoformat())

        orphan = False
        orphan_reason = ""
        if tx_id:
            if tx_id not in active_tx_map:
                orphan = True
                orphan_reason = "stale_transaction_link"
        elif status == "generated":
            orphan = True
            orphan_reason = "generated_without_transaction"
        elif status == "scheduled" and is_past:
            orphan = True
            orphan_reason = "past_scheduled_without_transaction"

        if not orphan:
            continue

        severity = "high" if orphan_reason in {"generated_without_transaction", "stale_transaction_link"} else "medium"
        issues.append(
            _build_issue(
                issue_type="orphan_recurring_instance",
                severity=severity,
                wallet_id=wallet_id,
                primary_entity_id=instance_id,
                date_hint=occ_iso,
                entity={
                    "doctype": INSTANCE_DTYPE,
                    "id": instance_id,
                    "date": occ_iso,
                    "rule_id": row.get("rule_id"),
                    "transaction_id": tx_id or None,
                },
                message="Recurring instance is orphaned (missing or stale transaction link)",
                details={
                    "status": status,
                    "occurrence_date": occ_iso,
                    "transaction_id": tx_id or None,
                    "reason": orphan_reason,
                },
                suggested_actions=[
                    {
                        "action": "link_or_delete",
                        "payload": {
                            "instance_id": instance_id,
                            "rule_id": row.get("rule_id"),
                            "occurrence_date": occ_iso,
                            "transaction_id": tx_id or None,
                            "mode": "skip" if is_past else "link",
                        },
                    }
                ],
            )
        )

    grouped: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
    for row in instances:
        rule_id = row.get("rule_id")
        occ_iso = _coerce_date_iso(row.get("occurrence_date"))
        if not rule_id or not occ_iso:
            continue
        grouped.setdefault((rule_id, occ_iso), []).append(row)

    for (rule_id, occ_iso), rows in grouped.items():
        tx_candidates = [
            row.get("transaction_id")
            for row in rows
            if row.get("transaction_id") and row.get("transaction_id") in active_tx_map
        ]
        distinct_tx_ids = sorted({tx_id for tx_id in tx_candidates if tx_id})
        duplicate = len(distinct_tx_ids) > 1
        if not duplicate:
            continue

        primary = f"{rule_id}:{occ_iso}"
        issues.append(
            _build_issue(
                issue_type="duplicate_recurring_output",
                severity="high",
                wallet_id=wallet_id,
                primary_entity_id=primary,
                date_hint=occ_iso,
                entity={
                    "doctype": RULE_DTYPE,
                    "id": rule_id,
                    "date": occ_iso,
                    "rule_id": rule_id,
                },
                message="Duplicate recurring outputs detected for the same rule/date",
                details={
                    "rule_id": rule_id,
                    "occurrence_date": occ_iso,
                    "instance_ids": [row.get("name") for row in rows if row.get("name")],
                    "transaction_ids": distinct_tx_ids,
                },
                suggested_actions=[
                    {
                        "action": "dedupe_keep_one",
                        "payload": {
                            "rule_id": rule_id,
                            "occurrence_date": occ_iso,
                            "instance_ids": [row.get("name") for row in rows if row.get("name")],
                            "transaction_ids": distinct_tx_ids,
                        },
                    }
                ],
            )
        )

    issues = [issue for issue in issues if issue.get("type") in SUPPORTED_ISSUE_TYPES]
    issues.sort(
        key=lambda row: (
            SEVERITY_ORDER.get((row.get("severity") or "low").lower(), 9),
            row.get("type") or "",
            (row.get("entity") or {}).get("date") or "",
            (row.get("entity") or {}).get("id") or "",
            row.get("issue_id") or "",
        )
    )

    unique: dict[str, Dict[str, Any]] = {}
    for issue in issues:
        issue_id = issue.get("issue_id")
        if issue_id and issue_id not in unique:
            unique[issue_id] = issue
    return list(unique.values())


def _resolve_transaction_name(wallet_id: str, transaction_id: str) -> Optional[str]:
    if not transaction_id:
        return None
    normalized = validate_client_id(transaction_id)
    by_name = frappe.get_value(TX_DTYPE, {"wallet_id": wallet_id, "name": normalized}, "name")
    if by_name:
        return by_name
    return frappe.get_value(TX_DTYPE, {"wallet_id": wallet_id, "client_id": normalized}, "name")


def _resolve_instance_name(wallet_id: str, instance_id: str) -> Optional[str]:
    if not instance_id:
        return None
    normalized = validate_client_id(instance_id)
    by_name = frappe.get_value(INSTANCE_DTYPE, {"wallet_id": wallet_id, "name": normalized, "is_deleted": 0}, "name")
    if by_name:
        return by_name
    return frappe.get_value(
        INSTANCE_DTYPE,
        {"wallet_id": wallet_id, "client_id": normalized, "is_deleted": 0},
        "name",
    )


def _income_allocation_status(wallet_id: str, tx_name: str, expected_amount: float) -> Dict[str, Any]:
    state = _query_income_allocation_map(wallet_id, [tx_name]).get(tx_name) or {"count": 0, "sum": 0.0}
    count = cint(state.get("count") or 0)
    total = flt(state.get("sum") or 0)
    return {
        "count": count,
        "sum": total,
        "is_complete": count > 0 and abs(total - flt(expected_amount, 2)) <= 0.01,
    }


def _apply_missing_income_allocation_fix(
    *,
    wallet_id: str,
    user: str,
    issue: Dict[str, Any],
    payload: Dict[str, Any],
) -> tuple[str, str]:
    tx_id = (payload.get("transaction_id") or (issue.get("entity") or {}).get("transaction_id") or "").strip()
    tx_name = _resolve_transaction_name(wallet_id, tx_id)
    if not tx_name:
        return "skipped", "transaction_not_found"

    tx_doc = ensure_income_transaction(tx_name, wallet_id)
    status = _income_allocation_status(wallet_id, tx_doc.name, flt(tx_doc.amount, 2))
    if status.get("is_complete"):
        return "skipped", "already_applied"

    raw_allocations = payload.get("allocations")
    mode = str(payload.get("mode") or "amount").strip().lower() or "amount"
    allocations: list[Dict[str, Any]] = []

    if isinstance(raw_allocations, list) and raw_allocations:
        allocations = [
            {"bucket": str(row.get("bucket") or "").strip(), "value": flt(row.get("value") or 0)}
            for row in raw_allocations
            if isinstance(row, dict)
        ]
    else:
        bucket_id = str(payload.get("bucket_id") or "").strip()
        if not bucket_id:
            active_buckets = _active_bucket_ids(wallet_id)
            if len(active_buckets) == 1:
                bucket_id = active_buckets[0]
        if not bucket_id:
            return "skipped", "bucket_required_for_income_allocation"
        allocations = [{"bucket": bucket_id, "value": flt(tx_doc.amount, 2)}]
        mode = "amount"

    if not allocations:
        return "skipped", "allocation_rows_required"

    set_manual_allocations_engine(user=user, tx_doc=tx_doc, mode=mode, allocations=allocations)
    return "applied", "ok"


def _apply_missing_expense_bucket_fix(
    *,
    wallet_id: str,
    user: str,
    issue: Dict[str, Any],
    payload: Dict[str, Any],
) -> tuple[str, str]:
    tx_id = (payload.get("transaction_id") or (issue.get("entity") or {}).get("transaction_id") or "").strip()
    tx_name = _resolve_transaction_name(wallet_id, tx_id)
    if not tx_name:
        return "skipped", "transaction_not_found"

    tx_doc = ensure_expense_transaction(tx_name, wallet_id)
    bucket_id = str(payload.get("bucket_id") or "").strip()
    if not bucket_id:
        return "skipped", "bucket_required"

    bucket_doc = ensure_bucket_wallet_scope(bucket_id, wallet_id)

    existing_name = frappe.get_value(
        TX_EXPENSE_BUCKET_DTYPE,
        {"wallet_id": wallet_id, "transaction_id": tx_doc.name, "is_deleted": 0},
        "name",
    )
    if existing_name:
        existing = frappe.get_doc(TX_EXPENSE_BUCKET_DTYPE, existing_name)
        if existing.bucket_id == bucket_doc.name and cint(existing.is_deleted or 0) == 0:
            return "skipped", "already_applied"
        existing.user = user
        existing.wallet_id = wallet_id
        existing.transaction_id = tx_doc.name
        existing.bucket_id = bucket_doc.name
        apply_common_sync_fields(existing, bump_version=True, mark_deleted=False)
        existing.save(ignore_permissions=True)
        return "applied", "ok"

    assignment = frappe.new_doc(TX_EXPENSE_BUCKET_DTYPE)
    assignment.user = user
    assignment.wallet_id = wallet_id
    assignment.transaction_id = tx_doc.name
    assignment.bucket_id = bucket_doc.name
    apply_common_sync_fields(assignment, bump_version=True, mark_deleted=False)
    assignment.save(ignore_permissions=True)
    return "applied", "ok"


def _apply_fx_ack_fix(
    *,
    wallet_id: str,
    user: str,
    device_id: str,
    issue_id: str,
    payload: Dict[str, Any],
) -> tuple[str, str]:
    acknowledge = payload.get("acknowledge")
    if acknowledge is False:
        return "skipped", "action_not_supported_without_acknowledge"

    existing = frappe.get_value(
        AUDIT_DTYPE,
        {
            "wallet_id": wallet_id,
            "event_type": "review.fx_missing_ack",
            "entity_client_id": issue_id,
            "status": "accepted",
        },
        "name",
    )
    if existing:
        return "skipped", "already_applied"

    audit = frappe.new_doc(AUDIT_DTYPE)
    audit.user = user
    audit.wallet_id = wallet_id
    audit.event_type = "review.fx_missing_ack"
    audit.device_id = device_id
    audit.related_entity_type = "review_issue"
    audit.related_entity_id = issue_id
    audit.entity_type = "review_issue"
    audit.entity_client_id = issue_id
    audit.status = "accepted"
    audit.payload_json = json.dumps(
        {
            "issue_id": issue_id,
            "acknowledged": True,
            "acknowledged_at": now_datetime().isoformat(),
        },
        ensure_ascii=False,
    )
    audit.insert(ignore_permissions=True)
    return "applied", "ok"


def _apply_orphan_fix(
    *,
    wallet_id: str,
    issue: Dict[str, Any],
    payload: Dict[str, Any],
) -> tuple[str, str]:
    instance_id = (payload.get("instance_id") or (issue.get("entity") or {}).get("id") or "").strip()
    instance_name = _resolve_instance_name(wallet_id, instance_id)
    if not instance_name:
        return "skipped", "already_applied"

    instance = frappe.get_doc(INSTANCE_DTYPE, instance_name)
    occ_date = get_datetime(instance.occurrence_date).date() if instance.occurrence_date else None
    today = now_datetime().date()

    link_tx_id = str(payload.get("transaction_id") or "").strip()
    if link_tx_id:
        tx_name = _resolve_transaction_name(wallet_id, link_tx_id)
        if not tx_name:
            return "skipped", "transaction_not_found"
        tx_doc = frappe.get_doc(TX_DTYPE, tx_name)
        if cint(tx_doc.is_deleted or 0):
            return "skipped", "transaction_deleted"
        if instance.transaction_id == tx_doc.name and (instance.status or "").strip().lower() == "generated":
            return "skipped", "already_applied"
        instance.transaction_id = tx_doc.name
        instance.status = "generated"
        instance.skip_reason = None
        instance.generated_at = now_datetime()
        apply_common_sync_fields(instance, bump_version=True, mark_deleted=False)
        instance.save(ignore_permissions=True)
        return "applied", "ok"

    mode = str(payload.get("mode") or "skip").strip().lower()
    if mode == "delete":
        if cint(instance.is_deleted or 0):
            return "skipped", "already_applied"
        apply_common_sync_fields(instance, bump_version=True, mark_deleted=True)
        instance.save(ignore_permissions=True)
        return "applied", "ok"

    if not occ_date or occ_date >= today:
        return "skipped", "future_orphan_requires_manual_link"

    reason = str(payload.get("reason") or "review_center_orphan_fix").strip() or "review_center_orphan_fix"
    changed = False
    if (instance.status or "").strip().lower() != "skipped":
        instance.status = "skipped"
        changed = True
    if (instance.skip_reason or "") != reason:
        instance.skip_reason = reason
        changed = True
    if instance.transaction_id:
        instance.transaction_id = None
        changed = True
    if instance.generated_at:
        instance.generated_at = None
        changed = True

    if not changed:
        return "skipped", "already_applied"

    apply_common_sync_fields(instance, bump_version=True, mark_deleted=False)
    instance.save(ignore_permissions=True)
    return "applied", "ok"


def _choose_keep_transaction(wallet_id: str, tx_ids: list[str]) -> tuple[Optional[str], list[str]]:
    if not tx_ids:
        return None, []
    rows = frappe.db.sql(
        """
        SELECT name, creation
        FROM `tabHisabi Transaction`
        WHERE wallet_id=%(wallet_id)s
          AND is_deleted=0
          AND name IN %(tx_ids)s
        ORDER BY creation ASC, name ASC
        """,
        {"wallet_id": wallet_id, "tx_ids": tuple(tx_ids)},
        as_dict=True,
    )
    ordered = [row.get("name") for row in rows if row.get("name")]
    if not ordered:
        return None, []
    keep = ordered[0]
    return keep, [row_id for row_id in ordered[1:] if row_id]


def _apply_duplicate_fix(
    *,
    wallet_id: str,
    issue: Dict[str, Any],
    payload: Dict[str, Any],
) -> tuple[str, str]:
    rule_id = str(payload.get("rule_id") or (issue.get("entity") or {}).get("rule_id") or "").strip()
    occ_date_raw = payload.get("occurrence_date") or (issue.get("entity") or {}).get("date")
    if not rule_id or not occ_date_raw:
        return "skipped", "rule_id_and_occurrence_date_required"

    occ_date = _parse_date(occ_date_raw, param="occurrence_date")
    rows = frappe.get_all(
        INSTANCE_DTYPE,
        filters={
            "wallet_id": wallet_id,
            "rule_id": rule_id,
            "occurrence_date": occ_date,
            "is_deleted": 0,
        },
        fields=["name", "transaction_id", "doc_version"],
        order_by="creation asc, name asc",
    )
    tx_ids = sorted({row.get("transaction_id") for row in rows if row.get("transaction_id")})
    keep_tx, duplicate_tx_ids = _choose_keep_transaction(wallet_id, tx_ids)

    if not keep_tx or not duplicate_tx_ids:
        return "skipped", "already_applied"

    changed = False
    for tx_id in duplicate_tx_ids:
        tx_doc = frappe.get_doc(TX_DTYPE, tx_id)
        if cint(tx_doc.is_deleted or 0):
            continue
        apply_common_sync_fields(tx_doc, bump_version=True, mark_deleted=True)
        tx_doc.save(ignore_permissions=True)
        changed = True

    for row in rows:
        instance_name = row.get("name")
        if not instance_name:
            continue
        tx_id = row.get("transaction_id")
        if tx_id not in duplicate_tx_ids:
            continue
        frappe.db.set_value(
            INSTANCE_DTYPE,
            instance_name,
            {
                "transaction_id": keep_tx,
                "status": "generated",
                "skip_reason": None,
                "doc_version": cint(row.get("doc_version") or 0) + 1,
                "server_modified": now_datetime(),
            },
            update_modified=False,
        )
        changed = True

    if not changed:
        return "skipped", "already_applied"
    return "applied", "ok"


def _build_stats(issues: list[Dict[str, Any]]) -> Dict[str, int]:
    high = sum(1 for issue in issues if (issue.get("severity") or "").lower() == "high")
    medium = sum(1 for issue in issues if (issue.get("severity") or "").lower() == "medium")
    low = sum(1 for issue in issues if (issue.get("severity") or "").lower() == "low")
    return {
        "total": len(issues),
        "high": high,
        "medium": medium,
        "low": low,
    }


@frappe.whitelist(allow_guest=False)
def issues(
    wallet_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    include_resolved: Optional[Any] = False,
    device_id: Optional[str] = None,
):
    payload = _request_payload()
    wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
    from_date = _resolve_param(from_date, "from_date") or payload.get("from_date")
    to_date = _resolve_param(to_date, "to_date") or payload.get("to_date")
    include_resolved_value = _resolve_param(include_resolved, "include_resolved")
    if include_resolved_value in (None, ""):
        include_resolved_value = payload.get("include_resolved")

    user, _device = require_device_token_auth()

    if not wallet_id:
        return _build_invalid_request("wallet_id is required", param="wallet_id")
    try:
        wallet_id = validate_client_id(wallet_id)
    except Exception:
        return _build_invalid_request("wallet_id is invalid", param="wallet_id")

    require_wallet_member(wallet_id, user, min_role="viewer")

    try:
        window_from, window_to = _resolve_window(from_date, to_date)
    except ValueError as exc:
        return _build_invalid_request(str(exc), param="from_date" if "from_date" in str(exc) else "to_date")

    include_resolved_bool = _parse_bool(include_resolved_value)
    review_issues = _collect_review_issues(
        wallet_id=wallet_id,
        user=user,
        from_date=window_from,
        to_date=window_to,
        include_resolved=include_resolved_bool,
    )

    generated_at = now_datetime().isoformat()
    return {
        "meta": {
            "wallet_id": wallet_id,
            "from_date": window_from.isoformat(),
            "to_date": window_to.isoformat(),
            "generated_at": generated_at,
            "server_time": generated_at,
            "version": _app_version(),
            "commit": _repo_commit(),
        },
        "issues": review_issues,
        "stats": _build_stats(review_issues),
    }


@frappe.whitelist(allow_guest=False)
def apply_fix(
    wallet_id: Optional[str] = None,
    fixes: Optional[Any] = None,
    device_id: Optional[str] = None,
):
    payload = _request_payload()
    wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
    fixes_value = fixes if fixes not in (None, "") else (_resolve_param(fixes, "fixes") or payload.get("fixes"))

    user, device = require_device_token_auth()

    if not wallet_id:
        return _build_invalid_request("wallet_id is required", param="wallet_id")
    try:
        wallet_id = validate_client_id(wallet_id)
    except Exception:
        return _build_invalid_request("wallet_id is invalid", param="wallet_id")

    require_wallet_member(wallet_id, user, min_role="member")

    if isinstance(fixes_value, str):
        try:
            fixes_value = json.loads(fixes_value)
        except Exception:
            return _build_invalid_request("fixes must be valid JSON", param="fixes")

    if not isinstance(fixes_value, list):
        return _build_invalid_request("fixes must be a list", param="fixes")

    today = now_datetime().date()
    active_issues = _collect_review_issues(
        wallet_id=wallet_id,
        user=user,
        from_date=today - datetime.timedelta(days=3650),
        to_date=today + datetime.timedelta(days=365),
        include_resolved=True,
    )
    issue_map = {row.get("issue_id"): row for row in active_issues if row.get("issue_id")}

    applied = 0
    skipped: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []

    for idx, entry in enumerate(fixes_value):
        if not isinstance(entry, dict):
            errors.append(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "fix entry must be an object",
                        "param": f"fixes[{idx}]",
                    }
                }
            )
            continue

        issue_id = str(entry.get("issue_id") or "").strip()
        action = str(entry.get("action") or "").strip()
        entry_payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        issue = issue_map.get(issue_id)

        if not issue_id:
            errors.append(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "issue_id is required",
                        "param": f"fixes[{idx}].issue_id",
                    }
                }
            )
            continue

        if not action:
            errors.append(
                {
                    "issue_id": issue_id,
                    "error": {
                        "code": "invalid_request",
                        "message": "action is required",
                        "param": f"fixes[{idx}].action",
                    },
                }
            )
            continue

        try:
            if issue and issue.get("type") == "missing_income_allocation" and action == "open_allocation":
                status, reason = _apply_missing_income_allocation_fix(
                    wallet_id=wallet_id,
                    user=user,
                    issue=issue,
                    payload=entry_payload,
                )
            elif issue and issue.get("type") == "missing_expense_bucket" and action == "assign_bucket":
                status, reason = _apply_missing_expense_bucket_fix(
                    wallet_id=wallet_id,
                    user=user,
                    issue=issue,
                    payload=entry_payload,
                )
            elif (issue and issue.get("type") == "fx_missing" and action == "add_fx_rate") or (
                not issue and action == "add_fx_rate"
            ):
                status, reason = _apply_fx_ack_fix(
                    wallet_id=wallet_id,
                    user=user,
                    device_id=device.device_id,
                    issue_id=issue_id,
                    payload=entry_payload,
                )
            elif issue and issue.get("type") == "orphan_recurring_instance" and action == "link_or_delete":
                status, reason = _apply_orphan_fix(
                    wallet_id=wallet_id,
                    issue=issue,
                    payload=entry_payload,
                )
            elif issue and issue.get("type") == "duplicate_recurring_output" and action == "dedupe_keep_one":
                status, reason = _apply_duplicate_fix(
                    wallet_id=wallet_id,
                    issue=issue,
                    payload=entry_payload,
                )
            elif not issue:
                status, reason = "skipped", "already_applied"
            else:
                status, reason = "skipped", "action_not_supported_for_issue"

            if status == "applied":
                applied += 1
            else:
                skipped.append({"issue_id": issue_id, "action": action, "reason": reason})
        except (InvalidBucketAllocationError, InvalidBucketExpenseAssignmentError, ValueError, frappe.ValidationError) as exc:
            frappe.clear_last_message()
            errors.append(
                {
                    "issue_id": issue_id,
                    "action": action,
                    "error": {
                        "code": "invalid_request",
                        "message": str(exc),
                        "param": f"fixes[{idx}]",
                    },
                }
            )

    return {
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
    }
