"""Recurring transactions APIs (v1)."""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime
from werkzeug.wrappers import Response

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.request_params import get_request_param
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member

RULE_DTYPE = "Hisabi Recurring Rule"
INSTANCE_DTYPE = "Hisabi Recurring Instance"
TX_DTYPE = "Hisabi Transaction"

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WEEKDAY_TO_INT = {code: idx for idx, code in enumerate(WEEKDAY_CODES)}


@dataclass
class OccurrenceCandidate:
    rule_id: str
    occurrence_date: datetime.date
    status: str = "generated"
    skip_reason: Optional[str] = None


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


def _build_recurring_error(
    code: str,
    message: str,
    *,
    fields: Optional[Dict[str, Any]] = None,
    status_code: int = 422,
) -> Response:
    payload: Dict[str, Any] = {"error": {"code": code, "message": message, "fields": fields or {}}}
    return _json_response(payload, status_code=status_code)


def _to_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    dt = get_datetime(value)
    return dt.isoformat() if dt else None


def _parse_date(value: Any, *, param: str) -> datetime.date:
    if not value:
        raise ValueError(f"{param} is required")
    parsed = get_datetime(value)
    if not parsed:
        raise ValueError(f"{param} is invalid")
    return parsed.date()


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


def _normalize_rule_doc(doc: frappe.model.document.Document) -> Dict[str, Any]:
    return {
        "id": doc.client_id or doc.name,
        "client_id": doc.client_id,
        "wallet_id": doc.wallet_id,
        "is_active": cint(doc.is_active or 0),
        "title": doc.title,
        "transaction_type": doc.transaction_type,
        "amount": flt(doc.amount, 2),
        "currency": doc.currency,
        "category_id": doc.category_id,
        "account_id": doc.account_id,
        "note": doc.note,
        "start_date": str(doc.start_date) if doc.start_date else None,
        "timezone": doc.timezone,
        "rrule_type": doc.rrule_type,
        "interval": cint(doc.interval or 1),
        "byweekday": doc.byweekday,
        "bymonthday": cint(doc.bymonthday or 0) or None,
        "end_mode": doc.end_mode,
        "until_date": str(doc.until_date) if doc.until_date else None,
        "resume_date": str(doc.resume_date) if getattr(doc, "resume_date", None) else None,
        "count": cint(doc.count or 0) or None,
        "last_generated_at": _to_iso(doc.last_generated_at),
        "created_from": doc.created_from,
        "doc_version": cint(doc.doc_version or 0),
        "server_modified": _to_iso(doc.server_modified),
        "is_deleted": cint(doc.is_deleted or 0),
        "deleted_at": _to_iso(doc.deleted_at),
    }


def _normalize_instance_doc(doc: frappe.model.document.Document) -> Dict[str, Any]:
    return {
        "id": doc.client_id or doc.name,
        "client_id": doc.client_id,
        "wallet_id": doc.wallet_id,
        "rule_id": doc.rule_id,
        "occurrence_date": str(doc.occurrence_date) if doc.occurrence_date else None,
        "transaction_id": doc.transaction_id,
        "status": doc.status,
        "generated_at": _to_iso(doc.generated_at),
        "skip_reason": doc.skip_reason,
        "doc_version": cint(doc.doc_version or 0),
        "server_modified": _to_iso(doc.server_modified),
        "is_deleted": cint(doc.is_deleted or 0),
        "deleted_at": _to_iso(doc.deleted_at),
    }


def _weekday_list(raw: Any, start_date: datetime.date) -> List[int]:
    values: List[str] = []
    if isinstance(raw, list):
        values = [str(v or "").strip().upper() for v in raw]
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = [str(v or "").strip().upper() for v in parsed]
        except Exception:
            values = []

    result: List[int] = []
    for code in values:
        idx = WEEKDAY_TO_INT.get(code)
        if idx is None:
            continue
        if idx not in result:
            result.append(idx)

    if not result:
        result = [start_date.weekday()]
    return result


def _month_day(raw: Any, start_date: datetime.date) -> int:
    month_day = cint(raw or 0)
    if month_day < 1:
        month_day = start_date.day
    return month_day


def _months_diff(base: datetime.date, target: datetime.date) -> int:
    return (target.year - base.year) * 12 + (target.month - base.month)


def _iter_daily(
    *,
    start_date: datetime.date,
    from_date: datetime.date,
    to_date: datetime.date,
    interval: int,
    count_limit: Optional[int],
    until_date: Optional[datetime.date],
) -> List[datetime.date]:
    effective_end = min(to_date, until_date) if until_date else to_date
    if effective_end < start_date:
        return []

    current = start_date
    emitted = 0
    occurrences: List[datetime.date] = []
    while current <= effective_end:
        if count_limit and emitted >= count_limit:
            break
        emitted += 1
        if current >= from_date:
            occurrences.append(current)
        current = current + datetime.timedelta(days=interval)
    return occurrences


def _iter_weekly(
    *,
    start_date: datetime.date,
    from_date: datetime.date,
    to_date: datetime.date,
    interval: int,
    weekdays: List[int],
    count_limit: Optional[int],
    until_date: Optional[datetime.date],
) -> List[datetime.date]:
    effective_end = min(to_date, until_date) if until_date else to_date
    if effective_end < start_date:
        return []

    current = start_date
    emitted = 0
    occurrences: List[datetime.date] = []
    base_week_start = start_date - datetime.timedelta(days=start_date.weekday())

    while current <= effective_end:
        week_start = current - datetime.timedelta(days=current.weekday())
        week_delta = ((week_start - base_week_start).days // 7)
        if week_delta >= 0 and week_delta % interval == 0 and current.weekday() in weekdays:
            if not count_limit or emitted < count_limit:
                emitted += 1
                if current >= from_date:
                    occurrences.append(current)
            else:
                break
        current = current + datetime.timedelta(days=1)

    return occurrences


def _iter_monthly(
    *,
    start_date: datetime.date,
    from_date: datetime.date,
    to_date: datetime.date,
    interval: int,
    month_day: int,
    count_limit: Optional[int],
    until_date: Optional[datetime.date],
) -> tuple[List[datetime.date], List[Dict[str, Any]]]:
    effective_end = min(to_date, until_date) if until_date else to_date
    if effective_end < start_date:
        return [], []

    cursor = datetime.date(start_date.year, start_date.month, 1)
    end_month = datetime.date(effective_end.year, effective_end.month, 1)

    emitted = 0
    occurrences: List[datetime.date] = []
    warnings: List[Dict[str, Any]] = []

    while cursor <= end_month:
        month_delta = _months_diff(start_date, cursor)
        if month_delta >= 0 and month_delta % interval == 0:
            next_month = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            last_day = (next_month - datetime.timedelta(days=1)).day
            if month_day > last_day:
                missing_date = datetime.date(cursor.year, cursor.month, last_day)
                if from_date <= missing_date <= effective_end:
                    warnings.append(
                        {
                            "occurrence_date": missing_date.isoformat(),
                            "reason": "invalid_day",
                            "message": f"day {month_day} is invalid for {cursor.year}-{cursor.month:02d}",
                        }
                    )
            else:
                occurrence = datetime.date(cursor.year, cursor.month, month_day)
                if occurrence >= start_date and occurrence >= from_date and occurrence <= effective_end:
                    if not count_limit or emitted < count_limit:
                        emitted += 1
                        occurrences.append(occurrence)
                    else:
                        break
                elif occurrence >= start_date and occurrence <= effective_end:
                    if count_limit and emitted >= count_limit:
                        break
                    emitted += 1

        if cursor.month == 12:
            cursor = datetime.date(cursor.year + 1, 1, 1)
        else:
            cursor = datetime.date(cursor.year, cursor.month + 1, 1)

    return occurrences, warnings


def _rule_occurrences(
    rule: frappe.model.document.Document,
    from_date: datetime.date,
    to_date: datetime.date,
) -> tuple[List[OccurrenceCandidate], List[Dict[str, Any]]]:
    start_date = get_datetime(rule.start_date).date()
    interval = cint(rule.interval or 1)
    end_mode = (rule.end_mode or "none").strip().lower()

    until_date = get_datetime(rule.until_date).date() if end_mode == "until" and rule.until_date else None
    count_limit = cint(rule.count or 0) if end_mode == "count" else None
    if count_limit is not None and count_limit <= 0:
        count_limit = None

    warnings: List[Dict[str, Any]] = []
    dates: List[datetime.date] = []

    rrule_type = (rule.rrule_type or "daily").strip().lower()
    if rrule_type == "daily":
        dates = _iter_daily(
            start_date=start_date,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            count_limit=count_limit,
            until_date=until_date,
        )
    elif rrule_type == "weekly":
        weekdays = _weekday_list(rule.byweekday, start_date)
        dates = _iter_weekly(
            start_date=start_date,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            weekdays=weekdays,
            count_limit=count_limit,
            until_date=until_date,
        )
    elif rrule_type == "monthly":
        month_day = _month_day(rule.bymonthday, start_date)
        dates, warnings = _iter_monthly(
            start_date=start_date,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            month_day=month_day,
            count_limit=count_limit,
            until_date=until_date,
        )

    candidates = [OccurrenceCandidate(rule_id=rule.name, occurrence_date=occ) for occ in dates]
    return candidates, warnings


def _existing_instance_rows(
    wallet_id: str, rule_ids: List[str], from_date: datetime.date, to_date: datetime.date
) -> Dict[tuple[str, str], Dict[str, Any]]:
    if not rule_ids:
        return {}

    rows = frappe.get_all(
        INSTANCE_DTYPE,
        filters={
            "wallet_id": wallet_id,
            "rule_id": ["in", rule_ids],
            "occurrence_date": ["between", [from_date, to_date]],
            "is_deleted": 0,
        },
        fields=["name", "client_id", "rule_id", "occurrence_date", "transaction_id", "status", "skip_reason"],
    )
    existing: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        date_value = get_datetime(row.occurrence_date).date().isoformat()
        existing[(row.rule_id, date_value)] = {
            "name": row.name,
            "client_id": row.client_id,
            "rule_id": row.rule_id,
            "occurrence_date": date_value,
            "transaction_id": row.transaction_id,
            "status": (row.status or "").strip().lower() or "scheduled",
            "skip_reason": row.skip_reason,
        }
    return existing


def _resolve_rule_name(wallet_id: str, rule_id: str) -> Optional[str]:
    normalized = validate_client_id(rule_id)
    return frappe.get_value(RULE_DTYPE, {"wallet_id": wallet_id, "client_id": normalized}, "name") or frappe.get_value(
        RULE_DTYPE,
        {"wallet_id": wallet_id, "name": normalized},
        "name",
    )


def _resolve_instance_name(instance_id: str) -> Optional[str]:
    normalized = validate_client_id(instance_id)
    return frappe.get_value(INSTANCE_DTYPE, {"client_id": normalized, "is_deleted": 0}, "name") or frappe.get_value(
        INSTANCE_DTYPE,
        {"name": normalized, "is_deleted": 0},
        "name",
    )


def _tx_client_id(rule_id: str, occurrence_date: datetime.date) -> str:
    digest = hashlib.sha1(f"{rule_id}:{occurrence_date.isoformat()}".encode("utf-8")).hexdigest()[:24]
    return f"rtx-{digest}"


def _instance_client_id(rule_id: str, occurrence_date: datetime.date) -> str:
    digest = hashlib.sha1(f"{rule_id}:{occurrence_date.isoformat()}:instance".encode("utf-8")).hexdigest()[:24]
    return f"rinst-{digest}"


def _ledger_op_id(wallet_id: str, op_id: str) -> str:
    return f"{wallet_id}:{op_id}"


def _get_sync_op_result(user: str, device_id: str, wallet_id: str, op_id: str) -> Optional[Dict[str, Any]]:
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


def _store_sync_op_result(
    *,
    user: str,
    device_id: str,
    wallet_id: str,
    op_id: str,
    entity_type: str,
    client_id: str,
    status: str,
    result: Dict[str, Any],
) -> None:
    ledger_op_id = _ledger_op_id(wallet_id, op_id)
    if frappe.db.exists("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": ledger_op_id}):
        return

    doc = frappe.new_doc("Hisabi Sync Op")
    doc.user = user
    doc.device_id = device_id
    doc.op_id = ledger_op_id
    doc.entity_type = entity_type
    doc.client_id = client_id
    doc.status = status
    doc.result_json = json.dumps(result, ensure_ascii=False)
    doc.server_modified = result.get("server_modified")
    doc.save(ignore_permissions=True)


def _create_or_get_transaction_for_occurrence(
    *,
    user: str,
    device_id: str,
    wallet_id: str,
    rule: frappe.model.document.Document,
    occurrence_date: datetime.date,
) -> tuple[Optional[str], Optional[str]]:
    tx_client_id = _tx_client_id(rule.name, occurrence_date)
    tx_op_id = f"recurring-tx:{rule.name}:{occurrence_date.isoformat()}"

    existing_result = _get_sync_op_result(user, device_id, wallet_id, tx_op_id)
    if existing_result and existing_result.get("transaction_id"):
        return existing_result.get("transaction_id"), None

    existing_name = frappe.get_value(TX_DTYPE, {"wallet_id": wallet_id, "client_id": tx_client_id}, "name")
    if existing_name:
        result = {"transaction_id": existing_name, "server_modified": _to_iso(frappe.get_value(TX_DTYPE, existing_name, "server_modified"))}
        _store_sync_op_result(
            user=user,
            device_id=device_id,
            wallet_id=wallet_id,
            op_id=tx_op_id,
            entity_type=TX_DTYPE,
            client_id=tx_client_id,
            status="duplicate",
            result=result,
        )
        return existing_name, None

    tx_type = (rule.transaction_type or "").strip().lower()
    if tx_type == "transfer":
        return None, "transfer_not_supported"

    if tx_type in {"income", "expense"} and not rule.account_id:
        return None, "account_required"
    if tx_type == "expense" and not rule.category_id:
        return None, "category_required"

    tx = frappe.new_doc(TX_DTYPE)
    tx.user = user
    tx.wallet_id = wallet_id
    tx.client_id = tx_client_id
    tx.name = tx_client_id
    tx.flags.name_set = True
    tx.transaction_type = tx_type
    tx.date_time = get_datetime(f"{occurrence_date.isoformat()} 00:00:00")
    tx.amount = flt(rule.amount, 2)
    tx.currency = rule.currency
    tx.account = rule.account_id
    tx.category = rule.category_id if tx_type != "income" else None
    tx.note = (rule.note or "").strip()
    apply_common_sync_fields(tx, bump_version=True, mark_deleted=False)
    tx.save(ignore_permissions=True)

    result = {
        "transaction_id": tx.name,
        "server_modified": _to_iso(tx.server_modified),
        "doc_version": tx.doc_version,
    }
    _store_sync_op_result(
        user=user,
        device_id=device_id,
        wallet_id=wallet_id,
        op_id=tx_op_id,
        entity_type=TX_DTYPE,
        client_id=tx_client_id,
        status="accepted",
        result=result,
    )
    return tx.name, None


def _simulate_transaction_for_occurrence(
    *,
    wallet_id: str,
    rule: frappe.model.document.Document,
    occurrence_date: datetime.date,
) -> tuple[Optional[str], Optional[str]]:
    tx_type = (rule.transaction_type or "").strip().lower()
    if tx_type == "transfer":
        return None, "transfer_not_supported"
    if tx_type in {"income", "expense"} and not rule.account_id:
        return None, "account_required"
    if tx_type == "expense" and not rule.category_id:
        return None, "category_required"

    tx_client_id = _tx_client_id(rule.name, occurrence_date)
    existing_name = frappe.get_value(TX_DTYPE, {"wallet_id": wallet_id, "client_id": tx_client_id}, "name")
    return (existing_name or tx_client_id), None


@frappe.whitelist(allow_guest=False)
def rules_list(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        user, _device = require_device_token_auth()
        if not wallet_id:
            return _build_invalid_request("wallet_id is required", param="wallet_id")
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="viewer")

        rows = frappe.get_all(
            RULE_DTYPE,
            filters={"wallet_id": wallet_id, "is_deleted": 0},
            order_by="server_modified desc, doc_version desc",
            pluck="name",
        )
        rules = [_normalize_rule_doc(frappe.get_doc(RULE_DTYPE, name)) for name in rows]
        return {"rules": rules, "server_time": now_datetime().isoformat()}
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_invalid_request(str(exc))
        raise


@frappe.whitelist(allow_guest=False)
def upsert_rule(
    wallet_id: Optional[str] = None,
    client_id: Optional[str] = None,
    is_active: Optional[int] = None,
    title: Optional[str] = None,
    transaction_type: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    category_id: Optional[str] = None,
    account_id: Optional[str] = None,
    note: Optional[str] = None,
    start_date: Optional[str] = None,
    timezone: Optional[str] = None,
    rrule_type: Optional[str] = None,
    interval: Optional[int] = None,
    byweekday: Optional[str] = None,
    bymonthday: Optional[int] = None,
    end_mode: Optional[str] = None,
    until_date: Optional[str] = None,
    count: Optional[int] = None,
    created_from: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        client_id = _resolve_param(client_id, "client_id") or payload.get("client_id")
        is_active = is_active if is_active is not None else payload.get("is_active")
        title = title or payload.get("title")
        transaction_type = transaction_type or payload.get("transaction_type")
        amount = amount if amount is not None else payload.get("amount")
        currency = currency or payload.get("currency")
        category_id = category_id or payload.get("category_id")
        account_id = account_id or payload.get("account_id")
        note = note if note is not None else payload.get("note")
        start_date = start_date or payload.get("start_date")
        timezone = timezone or payload.get("timezone")
        rrule_type = rrule_type or payload.get("rrule_type")
        interval = interval if interval is not None else payload.get("interval")
        byweekday = byweekday or payload.get("byweekday")
        bymonthday = bymonthday if bymonthday is not None else payload.get("bymonthday")
        end_mode = end_mode or payload.get("end_mode")
        until_date = until_date or payload.get("until_date")
        count = count if count is not None else payload.get("count")
        created_from = created_from or payload.get("created_from")
        user, _device = require_device_token_auth()
        if not wallet_id:
            return _build_invalid_request("wallet_id is required", param="wallet_id")
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")

        client_id = (client_id or "").strip() or f"rrule-{frappe.generate_hash(length=12)}"
        client_id = validate_client_id(client_id)

        existing_name = frappe.get_value(RULE_DTYPE, {"wallet_id": wallet_id, "client_id": client_id}, "name")
        doc = frappe.get_doc(RULE_DTYPE, existing_name) if existing_name else frappe.new_doc(RULE_DTYPE)
        is_new = not bool(existing_name)
        if is_new:
            doc.user = user
            doc.wallet_id = wallet_id
            doc.client_id = client_id
            doc.name = client_id
            doc.flags.name_set = True

        updates = {
            "is_active": is_active,
            "title": title,
            "transaction_type": transaction_type,
            "amount": amount,
            "currency": currency,
            "category_id": category_id,
            "account_id": account_id,
            "note": note,
            "start_date": start_date,
            "timezone": timezone,
            "rrule_type": rrule_type,
            "interval": interval,
            "byweekday": byweekday,
            "bymonthday": bymonthday,
            "end_mode": end_mode,
            "until_date": until_date,
            "count": count,
            "created_from": created_from,
            "wallet_id": wallet_id,
        }
        for key, value in updates.items():
            if value is not None:
                setattr(doc, key, value)

        apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
        doc.save(ignore_permissions=True)
        return {
            "status": "ok",
            "rule": _normalize_rule_doc(doc),
            "created": is_new,
            "server_time": now_datetime().isoformat(),
        }
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_invalid_request(str(exc))
        raise


@frappe.whitelist(allow_guest=False)
def toggle_rule(
    rule_id: str,
    wallet_id: Optional[str] = None,
    is_active: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        is_active = is_active if is_active is not None else payload.get("is_active")
        rule_id = _resolve_param(rule_id, "rule_id") or payload.get("rule_id")
        user, _device = require_device_token_auth()
        if not wallet_id:
            return _build_invalid_request("wallet_id is required", param="wallet_id")
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")

        if not rule_id:
            return _build_invalid_request("rule_id is required", param="rule_id")
        resolved_rule_id = validate_client_id(rule_id)

        name = frappe.get_value(RULE_DTYPE, {"wallet_id": wallet_id, "client_id": resolved_rule_id}, "name") or frappe.get_value(
            RULE_DTYPE,
            {"wallet_id": wallet_id, "name": resolved_rule_id},
            "name",
        )
        if not name:
            return _build_invalid_request("rule_id not found", param="rule_id")

        doc = frappe.get_doc(RULE_DTYPE, name)
        doc.is_active = cint(is_active if is_active is not None else 0)
        apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
        doc.save(ignore_permissions=True)

        return {"status": "ok", "rule": _normalize_rule_doc(doc), "server_time": now_datetime().isoformat()}
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_invalid_request(str(exc))
        raise


@frappe.whitelist(allow_guest=False)
def generate(
    wallet_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    dry_run: Any = 0,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        from_date = _resolve_param(from_date, "from_date") or payload.get("from_date")
        to_date = _resolve_param(to_date, "to_date") or payload.get("to_date")
        if dry_run in (0, "0", False, None, ""):
            dry_run = payload.get("dry_run", dry_run)
        user, device = require_device_token_auth()
        if not wallet_id:
            return _build_invalid_request("wallet_id is required", param="wallet_id")
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")

        try:
            from_day = _parse_date(from_date, param="from_date")
            to_day = _parse_date(to_date, param="to_date")
        except ValueError as exc:
            return _build_invalid_request(str(exc))

        if to_day < from_day:
            return _build_invalid_request("to_date must be greater than or equal to from_date", param="to_date")

        is_dry = _parse_bool(dry_run)

        rule_names = frappe.get_all(RULE_DTYPE, filters={"wallet_id": wallet_id, "is_deleted": 0}, pluck="name", order_by="name asc")
        rules = [frappe.get_doc(RULE_DTYPE, name) for name in rule_names]
        existing = _existing_instance_rows(wallet_id, rule_names, from_day, to_day)

        generated_count = 0
        skipped_count = 0
        created_instance_ids: List[str] = []
        updated_instance_ids: List[str] = []
        preview: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        for rule in rules:
            resume_date = get_datetime(rule.resume_date).date() if getattr(rule, "resume_date", None) else None
            if cint(rule.is_active or 0) != 1 and not resume_date:
                continue

            effective_from = from_day
            if resume_date and resume_date > effective_from:
                effective_from = resume_date
            if effective_from > to_day:
                continue

            candidates, rule_warnings = _rule_occurrences(rule, effective_from, to_day)
            for warning in rule_warnings:
                warnings.append({"rule_id": rule.client_id or rule.name, **warning})

            changed_rule = False
            for candidate in candidates:
                key = (rule.name, candidate.occurrence_date.isoformat())
                existing_row = existing.get(key)
                tx_id: Optional[str] = None
                skip_reason: Optional[str] = None
                status: str = "exists"

                if existing_row:
                    if existing_row["status"] == "scheduled" and not existing_row.get("transaction_id"):
                        if is_dry:
                            tx_id, skip_reason = _simulate_transaction_for_occurrence(
                                wallet_id=wallet_id,
                                rule=rule,
                                occurrence_date=candidate.occurrence_date,
                            )
                            status = "generated" if tx_id else "skipped"
                            if status == "generated":
                                generated_count += 1
                            else:
                                skipped_count += 1
                                warnings.append(
                                    {
                                        "rule_id": rule.client_id or rule.name,
                                        "occurrence_date": candidate.occurrence_date.isoformat(),
                                        "reason": skip_reason or "skipped",
                                    }
                                )
                            preview.append(
                                {
                                    "rule_id": rule.client_id or rule.name,
                                    "occurrence_date": candidate.occurrence_date.isoformat(),
                                    "status": status,
                                    "transaction_id": tx_id,
                                    "skip_reason": skip_reason,
                                    "existing_status": "scheduled",
                                }
                            )
                            continue

                        tx_id, skip_reason = _create_or_get_transaction_for_occurrence(
                            user=user,
                            device_id=device.device_id,
                            wallet_id=wallet_id,
                            rule=rule,
                            occurrence_date=candidate.occurrence_date,
                        )
                        status = "generated" if tx_id else "skipped"
                        instance_doc = frappe.get_doc(INSTANCE_DTYPE, existing_row["name"])
                        instance_doc.transaction_id = tx_id
                        instance_doc.status = status
                        instance_doc.generated_at = now_datetime()
                        instance_doc.skip_reason = skip_reason
                        apply_common_sync_fields(instance_doc, bump_version=True, mark_deleted=False)
                        instance_doc.save(ignore_permissions=True)
                        updated_instance_ids.append(instance_doc.client_id or instance_doc.name)
                        changed_rule = True
                        preview.append(_normalize_instance_doc(instance_doc))
                        if status == "generated":
                            generated_count += 1
                        else:
                            skipped_count += 1
                            warnings.append(
                                {
                                    "rule_id": rule.client_id or rule.name,
                                    "occurrence_date": candidate.occurrence_date.isoformat(),
                                    "reason": skip_reason or "skipped",
                                }
                            )
                        existing[key] = {
                            "name": instance_doc.name,
                            "client_id": instance_doc.client_id,
                            "rule_id": instance_doc.rule_id,
                            "occurrence_date": candidate.occurrence_date.isoformat(),
                            "transaction_id": instance_doc.transaction_id,
                            "status": instance_doc.status,
                            "skip_reason": instance_doc.skip_reason,
                        }
                        continue

                    skipped_count += 1
                    preview.append(
                        {
                            "rule_id": rule.client_id or rule.name,
                            "occurrence_date": candidate.occurrence_date.isoformat(),
                            "status": "exists",
                            "existing_status": existing_row.get("status"),
                        }
                    )
                    continue

                if is_dry:
                    tx_id, skip_reason = _simulate_transaction_for_occurrence(wallet_id=wallet_id, rule=rule, occurrence_date=candidate.occurrence_date)
                    status = "generated" if tx_id else "skipped"
                else:
                    tx_id, skip_reason = _create_or_get_transaction_for_occurrence(
                        user=user,
                        device_id=device.device_id,
                        wallet_id=wallet_id,
                        rule=rule,
                        occurrence_date=candidate.occurrence_date,
                    )
                    status = "generated" if tx_id else "skipped"

                if is_dry:
                    if status == "generated":
                        generated_count += 1
                    else:
                        skipped_count += 1
                        warnings.append(
                            {
                                "rule_id": rule.client_id or rule.name,
                                "occurrence_date": candidate.occurrence_date.isoformat(),
                                "reason": skip_reason or "skipped",
                            }
                        )
                    preview.append(
                        {
                            "rule_id": rule.client_id or rule.name,
                            "occurrence_date": candidate.occurrence_date.isoformat(),
                            "status": status,
                            "transaction_id": tx_id,
                            "skip_reason": skip_reason,
                        }
                    )
                    continue

                instance = frappe.new_doc(INSTANCE_DTYPE)
                instance.user = user
                instance.wallet_id = wallet_id
                instance.client_id = _instance_client_id(rule.name, candidate.occurrence_date)
                instance.name = instance.client_id
                instance.flags.name_set = True
                instance.rule_id = rule.name
                instance.occurrence_date = candidate.occurrence_date
                instance.transaction_id = tx_id
                instance.status = status
                instance.generated_at = now_datetime()
                instance.skip_reason = skip_reason
                apply_common_sync_fields(instance, bump_version=True, mark_deleted=False)
                instance.save(ignore_permissions=True)
                changed_rule = True

                created_instance_ids.append(instance.client_id)
                existing[key] = {
                    "name": instance.name,
                    "client_id": instance.client_id,
                    "rule_id": instance.rule_id,
                    "occurrence_date": candidate.occurrence_date.isoformat(),
                    "transaction_id": instance.transaction_id,
                    "status": instance.status,
                    "skip_reason": instance.skip_reason,
                }
                if status == "generated":
                    generated_count += 1
                else:
                    skipped_count += 1
                    warnings.append(
                        {
                            "rule_id": rule.client_id or rule.name,
                            "occurrence_date": candidate.occurrence_date.isoformat(),
                            "reason": skip_reason or "skipped",
                        }
                    )

                preview.append(_normalize_instance_doc(instance))

            if not is_dry and changed_rule:
                rule.last_generated_at = now_datetime()
                apply_common_sync_fields(rule, bump_version=True, mark_deleted=False)
                rule.save(ignore_permissions=True)

        return {
            "status": "ok",
            "dry_run": is_dry,
            "generated": generated_count,
            "skipped": skipped_count,
            "created_instance_ids": created_instance_ids,
            "updated_instance_ids": updated_instance_ids,
            "warnings": warnings,
            "preview": preview,
            "server_time": now_datetime().isoformat(),
        }
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_invalid_request(str(exc))
        raise


@frappe.whitelist(allow_guest=False)
def apply_changes(
    rule_id: Optional[str] = None,
    wallet_id: Optional[str] = None,
    mode: Optional[str] = None,
    from_date: Optional[str] = None,
    horizon_days: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        rule_id = _resolve_param(rule_id, "rule_id") or payload.get("rule_id")
        mode = (_resolve_param(mode, "mode") or payload.get("mode") or "future_only").strip().lower()
        from_date = _resolve_param(from_date, "from_date") or payload.get("from_date")
        horizon_days = horizon_days if horizon_days is not None else payload.get("horizon_days")
        user, _device = require_device_token_auth()
        if not wallet_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "wallet_id is required", fields={"wallet_id": "required"})
        if not rule_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "rule_id is required", fields={"rule_id": "required"})
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")
        if mode not in {"future_only", "rebuild_scheduled"}:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "mode is invalid", fields={"mode": "invalid"})

        resolved_name = _resolve_rule_name(wallet_id, rule_id)
        if not resolved_name:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "rule_id not found", fields={"rule_id": "not_found"})
        rule = frappe.get_doc(RULE_DTYPE, resolved_name)
        if rule.wallet_id != wallet_id:
            return _build_recurring_error(
                "RECURRING_CONFLICT",
                "rule belongs to a different wallet",
                fields={"wallet_id": "mismatch", "rule_id": rule.client_id or rule.name},
            )

        start_day = _parse_date(from_date, param="from_date") if from_date else now_datetime().date()
        horizon = cint(horizon_days or 60)
        if horizon < 1 or horizon > 365:
            return _build_recurring_error(
                "RECURRING_VALIDATION_ERROR",
                "horizon_days must be between 1 and 365",
                fields={"horizon_days": "out_of_range"},
            )
        to_day = start_day + datetime.timedelta(days=horizon)

        deleted_count = 0
        created_count = 0
        kept_count = 0
        warnings: List[Dict[str, Any]] = []

        if mode == "rebuild_scheduled":
            existing_rows = frappe.get_all(
                INSTANCE_DTYPE,
                filters={
                    "wallet_id": wallet_id,
                    "rule_id": rule.name,
                    "is_deleted": 0,
                    "occurrence_date": [">=", start_day],
                    "status": "scheduled",
                },
                fields=["name", "transaction_id"],
            )
            for row in existing_rows:
                if row.transaction_id:
                    kept_count += 1
                    warnings.append(
                        {
                            "code": "RECURRING_TX_EXISTS",
                            "message": "scheduled instance has transaction and was kept",
                            "instance_id": row.name,
                        }
                    )
                    continue
                instance_doc = frappe.get_doc(INSTANCE_DTYPE, row.name)
                apply_common_sync_fields(instance_doc, bump_version=True, mark_deleted=True)
                instance_doc.save(ignore_permissions=True)
                deleted_count += 1

            existing_after_delete = _existing_instance_rows(wallet_id, [rule.name], start_day, to_day)
            candidates, rule_warnings = _rule_occurrences(rule, start_day, to_day)
            for warning in rule_warnings:
                warnings.append({"code": "RECURRING_VALIDATION_ERROR", "rule_id": rule.client_id or rule.name, **warning})

            for candidate in candidates:
                key = (rule.name, candidate.occurrence_date.isoformat())
                if key in existing_after_delete:
                    kept_count += 1
                    warnings.append(
                        {
                            "code": "RECURRING_INSTANCE_EXISTS",
                            "rule_id": rule.client_id or rule.name,
                            "occurrence_date": candidate.occurrence_date.isoformat(),
                        }
                    )
                    continue

                instance = frappe.new_doc(INSTANCE_DTYPE)
                instance.user = user
                instance.wallet_id = wallet_id
                instance.client_id = _instance_client_id(rule.name, candidate.occurrence_date)
                instance.name = instance.client_id
                instance.flags.name_set = True
                instance.rule_id = rule.name
                instance.occurrence_date = candidate.occurrence_date
                instance.transaction_id = None
                instance.status = "scheduled"
                instance.generated_at = None
                instance.skip_reason = None
                apply_common_sync_fields(instance, bump_version=True, mark_deleted=False)
                instance.save(ignore_permissions=True)
                created_count += 1
                existing_after_delete[key] = {
                    "name": instance.name,
                    "client_id": instance.client_id,
                    "rule_id": instance.rule_id,
                    "occurrence_date": candidate.occurrence_date.isoformat(),
                    "transaction_id": None,
                    "status": "scheduled",
                    "skip_reason": None,
                }
        else:
            kept_count = frappe.db.count(
                INSTANCE_DTYPE,
                {"wallet_id": wallet_id, "rule_id": rule.name, "occurrence_date": [">=", start_day], "is_deleted": 0},
            )

        return {
            "status": "ok",
            "mode": mode,
            "from_date": start_day.isoformat(),
            "to_date": to_day.isoformat(),
            "counts": {"deleted": deleted_count, "created": created_count, "kept": kept_count},
            "warnings": warnings,
            "rule": _normalize_rule_doc(rule),
            "server_time": now_datetime().isoformat(),
        }
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", str(exc), fields={})
        raise


@frappe.whitelist(allow_guest=False)
def skip_instance(
    instance_id: Optional[str] = None,
    wallet_id: Optional[str] = None,
    reason: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        instance_id = _resolve_param(instance_id, "instance_id") or payload.get("instance_id")
        reason = _resolve_param(reason, "reason") or payload.get("reason")
        user, _device = require_device_token_auth()
        if not instance_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "instance_id is required", fields={"instance_id": "required"})

        instance_name = _resolve_instance_name(instance_id)
        if not instance_name:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "instance_id not found", fields={"instance_id": "not_found"})
        instance = frappe.get_doc(INSTANCE_DTYPE, instance_name)

        target_wallet_id = wallet_id or instance.wallet_id
        if not target_wallet_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "wallet_id is required", fields={"wallet_id": "required"})
        target_wallet_id = validate_client_id(target_wallet_id)
        require_wallet_member(target_wallet_id, user, min_role="member")
        if instance.wallet_id != target_wallet_id:
            return _build_recurring_error(
                "RECURRING_CONFLICT",
                "instance belongs to a different wallet",
                fields={"wallet_id": "mismatch", "instance_id": instance.client_id or instance.name},
            )

        warnings: List[Dict[str, Any]] = []
        if instance.transaction_id:
            warnings.append(
                {
                    "code": "RECURRING_TX_EXISTS",
                    "message": "transaction already exists and was not deleted",
                    "warning": "tx_exists",
                    "transaction_id": instance.transaction_id,
                }
            )

        instance.status = "skipped"
        instance.skip_reason = (reason or instance.skip_reason or "manual_skip").strip()[:140]
        apply_common_sync_fields(instance, bump_version=True, mark_deleted=False)
        instance.save(ignore_permissions=True)

        return {
            "status": "ok",
            "instance": _normalize_instance_doc(instance),
            "warnings": warnings,
            "server_time": now_datetime().isoformat(),
        }
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", str(exc), fields={})
        raise


@frappe.whitelist(allow_guest=False)
def pause_until(
    rule_id: Optional[str] = None,
    until_date: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        rule_id = _resolve_param(rule_id, "rule_id") or payload.get("rule_id")
        until_date = _resolve_param(until_date, "until_date") or payload.get("until_date")
        user, _device = require_device_token_auth()
        if not wallet_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "wallet_id is required", fields={"wallet_id": "required"})
        if not rule_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "rule_id is required", fields={"rule_id": "required"})
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="member")

        try:
            pause_day = _parse_date(until_date, param="until_date")
        except ValueError:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "until_date is invalid", fields={"until_date": "invalid"})

        resolved_name = _resolve_rule_name(wallet_id, rule_id)
        if not resolved_name:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "rule_id not found", fields={"rule_id": "not_found"})

        rule = frappe.get_doc(RULE_DTYPE, resolved_name)
        if rule.wallet_id != wallet_id:
            return _build_recurring_error(
                "RECURRING_CONFLICT",
                "rule belongs to a different wallet",
                fields={"wallet_id": "mismatch", "rule_id": rule.client_id or rule.name},
            )

        rule.is_active = 0
        rule.resume_date = pause_day
        apply_common_sync_fields(rule, bump_version=True, mark_deleted=False)
        rule.save(ignore_permissions=True)
        return {"status": "ok", "rule": _normalize_rule_doc(rule), "server_time": now_datetime().isoformat()}
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", str(exc), fields={})
        raise


@frappe.whitelist(allow_guest=False)
def preview(
    wallet_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    try:
        payload = _request_payload()
        wallet_id = _resolve_param(wallet_id, "wallet_id") or payload.get("wallet_id")
        rule_id = _resolve_param(rule_id, "rule_id") or payload.get("rule_id")
        from_date = _resolve_param(from_date, "from") or _resolve_param(from_date, "from_date") or payload.get("from") or payload.get("from_date")
        to_date = _resolve_param(to_date, "to") or _resolve_param(to_date, "to_date") or payload.get("to") or payload.get("to_date")
        user, _device = require_device_token_auth()
        if not wallet_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "wallet_id is required", fields={"wallet_id": "required"})
        if not rule_id:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "rule_id is required", fields={"rule_id": "required"})
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="viewer")

        try:
            from_day = _parse_date(from_date, param="from")
            to_day = _parse_date(to_date, param="to")
        except ValueError as exc:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", str(exc), fields={})
        if to_day < from_day:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "to must be greater than or equal to from", fields={"to": "invalid_range"})

        resolved_name = _resolve_rule_name(wallet_id, rule_id)
        if not resolved_name:
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", "rule_id not found", fields={"rule_id": "not_found"})
        rule = frappe.get_doc(RULE_DTYPE, resolved_name)

        candidates, rule_warnings = _rule_occurrences(rule, from_day, to_day)
        existing = _existing_instance_rows(wallet_id, [rule.name], from_day, to_day)
        occurrences: List[Dict[str, Any]] = []
        for candidate in candidates:
            key = (rule.name, candidate.occurrence_date.isoformat())
            existing_row = existing.get(key)
            occurrences.append(
                {
                    "rule_id": rule.client_id or rule.name,
                    "occurrence_date": candidate.occurrence_date.isoformat(),
                    "would_create": existing_row is None,
                    "existing_status": existing_row.get("status") if existing_row else None,
                }
            )

        warnings: List[Dict[str, Any]] = []
        for warning in rule_warnings:
            warnings.append({"code": "RECURRING_VALIDATION_ERROR", "rule_id": rule.client_id or rule.name, **warning})
        return {
            "status": "ok",
            "rule_id": rule.client_id or rule.name,
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
            "occurrences": occurrences,
            "warnings": warnings,
            "server_time": now_datetime().isoformat(),
        }
    except Exception as exc:
        if isinstance(exc, frappe.ValidationError):
            frappe.clear_last_message()
            return _build_recurring_error("RECURRING_VALIDATION_ERROR", str(exc), fields={})
        raise
