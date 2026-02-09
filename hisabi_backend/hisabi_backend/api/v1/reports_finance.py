"""Financial reporting APIs (v1/v2 contract)."""

from __future__ import annotations

import datetime
import json
from collections import defaultdict
from typing import Any, Dict, Optional

import frappe
from frappe.utils import flt, get_datetime, now_datetime
from werkzeug.wrappers import Response

from hisabi_backend.utils.request_params import get_request_param
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


def _build_invalid_request(message: str, *, param: Optional[str] = None, status_code: int = 422) -> Response:
    payload: Dict[str, Any] = {"error": {"code": "invalid_request", "message": message}}
    if param:
        payload["error"]["param"] = param
    response = Response()
    response.mimetype = "application/json"
    response.status_code = status_code
    response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return response


def _resolve_wallet_id_param(wallet_id: Optional[str]) -> str | Response:
    # Be explicit: Frappe RPC sometimes doesn't populate `wallet_id` into function args.
    wallet_id = wallet_id or frappe.form_dict.get("wallet_id") or get_request_param("wallet_id")
    if not wallet_id:
        return _build_invalid_request("wallet_id is required", param="wallet_id")
    try:
        return validate_client_id(wallet_id)
    except Exception:
        return _build_invalid_request("wallet_id is invalid", param="wallet_id")


def _resolve_date_range(
    *,
    from_date: Optional[str],
    to_date: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    return (from_date or date_from, to_date or date_to)


def _normalize_currency(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = (value or "").strip().upper()
    return normalized or None


def _normalize_type_filter(value: Optional[str]) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        raw_values = [v.strip().lower() for v in value.split(",")]
    else:
        raw_values = [str(value).strip().lower()]
    allowed = {"income", "expense", "transfer"}
    return [v for v in raw_values if v in allowed]


def _build_tx_filters(
    *,
    wallet_id: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    type_filter: Optional[str] = None,
    table_alias: Optional[str] = None,
) -> tuple[list[str], Dict[str, Any]]:
    prefix = f"{table_alias}." if table_alias else ""
    params: Dict[str, Any] = {"wallet_id": wallet_id}
    filters = [f"{prefix}wallet_id=%(wallet_id)s", f"{prefix}is_deleted=0"]

    if from_date:
        params["from_date"] = get_datetime(from_date)
        filters.append(f"{prefix}date_time >= %(from_date)s")
    if to_date:
        params["to_date"] = get_datetime(to_date)
        filters.append(f"{prefix}date_time <= %(to_date)s")

    normalized_currency = _normalize_currency(currency)
    if normalized_currency:
        params["currency"] = normalized_currency
        filters.append(f"{prefix}currency = %(currency)s")

    if account_id:
        params["account_id"] = account_id
        # Account filter should include both outgoing and incoming transfer legs.
        filters.append(f"({prefix}account = %(account_id)s OR {prefix}to_account = %(account_id)s)")

    if category_id:
        params["category_id"] = category_id
        filters.append(f"{prefix}category = %(category_id)s")

    normalized_types = _normalize_type_filter(type_filter)
    if normalized_types:
        params["type_filter"] = tuple(normalized_types)
        filters.append(f"{prefix}transaction_type IN %(type_filter)s")

    return filters, params


def _resolve_wallet_base_currency(wallet_id: str, user: str) -> str:
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
    return _normalize_currency(currency) or "USD"


def _parse_date(value: Any) -> datetime.date:
    dt = get_datetime(value) or now_datetime()
    return dt.date()


def _as_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    dt = get_datetime(value)
    return dt.isoformat() if dt else None


def _resolve_fx_rate(
    *,
    wallet_id: str,
    source_currency: str,
    target_currency: str,
    tx_date: datetime.date,
    cache: Dict[tuple[str, str, str, str], Optional[float]],
) -> Optional[float]:
    src = _normalize_currency(source_currency)
    dst = _normalize_currency(target_currency)
    if not src or not dst:
        return None
    if src == dst:
        return 1.0

    cache_key = (wallet_id, src, dst, tx_date.isoformat())
    if cache_key in cache:
        return cache[cache_key]

    direct = frappe.db.sql(
        """
        SELECT rate
        FROM `tabHisabi FX Rate`
        WHERE wallet_id=%s
          AND is_deleted=0
          AND base_currency=%s
          AND quote_currency=%s
          AND DATE(effective_date) <= %s
        ORDER BY effective_date DESC, server_modified DESC, name DESC
        LIMIT 1
        """,
        (wallet_id, src, dst, tx_date),
        as_dict=True,
    )
    if direct:
        rate = flt(direct[0].get("rate") or 0)
        cache[cache_key] = rate if rate > 0 else None
        return cache[cache_key]

    inverse = frappe.db.sql(
        """
        SELECT rate
        FROM `tabHisabi FX Rate`
        WHERE wallet_id=%s
          AND is_deleted=0
          AND base_currency=%s
          AND quote_currency=%s
          AND DATE(effective_date) <= %s
        ORDER BY effective_date DESC, server_modified DESC, name DESC
        LIMIT 1
        """,
        (wallet_id, dst, src, tx_date),
        as_dict=True,
    )
    if inverse:
        reverse_rate = flt(inverse[0].get("rate") or 0)
        if reverse_rate > 0:
            cache[cache_key] = 1.0 / reverse_rate
            return cache[cache_key]

    cache[cache_key] = None
    return None


def _append_fx_warning(
    warnings: list[Dict[str, Any]],
    seen: set[str],
    *,
    tx: Dict[str, Any],
    base_currency: str,
) -> None:
    tx_id = (tx.get("name") or "").strip()
    if not tx_id or tx_id in seen:
        return
    seen.add(tx_id)
    warnings.append(
        {
            "code": "fx_missing",
            "message": "FX rate is missing for conversion to wallet base currency",
            "tx_id": tx_id,
            "currency": _normalize_currency(tx.get("currency")),
            "base_currency": base_currency,
            "date_time": _as_iso(tx.get("date_time")),
        }
    )


def _tx_amount_in_base(
    *,
    tx: Dict[str, Any],
    wallet_id: str,
    base_currency: str,
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]],
    warnings: list[Dict[str, Any]],
    warning_seen: set[str],
) -> Optional[float]:
    amount = flt(tx.get("amount") or 0)
    currency = _normalize_currency(tx.get("currency"))
    if not amount:
        return 0.0

    if currency == base_currency:
        return amount

    amount_base = tx.get("amount_base")
    if amount_base is not None:
        amount_base_value = flt(amount_base)
        if amount_base_value or not amount:
            return amount_base_value

    if not currency:
        _append_fx_warning(warnings, warning_seen, tx=tx, base_currency=base_currency)
        return None

    tx_date = _parse_date(tx.get("date_time"))
    rate = _resolve_fx_rate(
        wallet_id=wallet_id,
        source_currency=currency,
        target_currency=base_currency,
        tx_date=tx_date,
        cache=fx_cache,
    )
    if rate and rate > 0:
        return flt(amount * rate)

    # No guessing: when conversion is missing we emit an explicit warning and skip totals.
    _append_fx_warning(warnings, warning_seen, tx=tx, base_currency=base_currency)
    return None


def _query_transactions(*, filters: list[str], params: Dict[str, Any]) -> list[Dict[str, Any]]:
    return frappe.db.sql(
        f"""
        SELECT
            name,
            transaction_type,
            amount,
            amount_base,
            currency,
            account,
            to_account,
            category,
            date_time
        FROM `tabHisabi Transaction`
        WHERE {' AND '.join(filters)}
        ORDER BY COALESCE(date_time, creation) ASC, name ASC
        """,
        params,
        as_dict=True,
    )


def _with_warnings(payload: Dict[str, Any], warnings: list[Dict[str, Any]]) -> Dict[str, Any]:
    output = dict(payload)
    output["warnings"] = warnings or []
    return output


UNALLOCATED_BUCKET_ID = "unallocated"


def _resolve_report_currency(*, wallet_id: str, user: str, currency: Optional[str]) -> str:
    normalized = _normalize_currency(currency)
    if normalized:
        return normalized
    return _resolve_wallet_base_currency(wallet_id, user)


def _resolve_currency_param(currency: Optional[str]) -> Optional[str]:
    request_currency = currency or frappe.form_dict.get("currency") or get_request_param("currency")
    request = getattr(frappe.local, "request", None)
    if not request_currency and request and getattr(request, "args", None):
        request_currency = request.args.get("currency")
    return _normalize_currency(request_currency)


def _append_generic_fx_warning(
    warnings: list[Dict[str, Any]],
    warning_state: Dict[str, bool],
) -> None:
    if warning_state.get("fx_missing"):
        return
    warning_state["fx_missing"] = True
    warnings.append(
        {
            "code": "fx_missing",
            "message": "Some amounts are excluded due to missing FX rates.",
        }
    )


def _convert_amount_to_currency(
    *,
    amount: Any,
    source_currency: Optional[str],
    target_currency: str,
    tx_date_time: Any,
    wallet_id: str,
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]],
    warnings: list[Dict[str, Any]],
    warning_state: Dict[str, bool],
) -> Optional[float]:
    amount_value = flt(amount or 0)
    if not amount_value:
        return 0.0

    source = _normalize_currency(source_currency)
    target = _normalize_currency(target_currency)
    if not source or not target:
        _append_generic_fx_warning(warnings, warning_state)
        return None
    if source == target:
        return amount_value

    tx_date = _parse_date(tx_date_time)
    rate = _resolve_fx_rate(
        wallet_id=wallet_id,
        source_currency=source,
        target_currency=target,
        tx_date=tx_date,
        cache=fx_cache,
    )
    if rate and rate > 0:
        return flt(amount_value * rate)

    _append_generic_fx_warning(warnings, warning_state)
    return None


def _get_wallet_bucket_meta(wallet_id: str) -> Dict[str, Dict[str, str]]:
    rows = frappe.get_all(
        "Hisabi Bucket",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "title", "bucket_name"],
    )
    meta: Dict[str, Dict[str, str]] = {}
    for row in rows:
        bucket_id = row.get("name")
        if not bucket_id:
            continue
        title = (row.get("title") or row.get("bucket_name") or bucket_id).strip() or bucket_id
        meta[bucket_id] = {
            "bucket_id": bucket_id,
            "bucket_title": title,
        }
    return meta


def _group_allocation_rows(
    rows: list[Dict[str, Any]],
    *,
    tx_key: str,
    bucket_key: str,
    amount_key: str = "amount",
    currency_key: Optional[str] = None,
    bucket_meta: Dict[str, Dict[str, str]],
) -> Dict[str, list[Dict[str, Any]]]:
    grouped: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        tx_id = row.get(tx_key)
        bucket_id = row.get(bucket_key)
        if not tx_id or not bucket_id:
            continue
        if bucket_id not in bucket_meta:
            continue
        amount = flt(row.get(amount_key) or 0)
        if not amount:
            continue

        item: Dict[str, Any] = {
            "bucket_id": bucket_id,
            "amount": amount,
        }
        if currency_key:
            item["currency"] = _normalize_currency(row.get(currency_key))
        grouped[str(tx_id)].append(item)
    return grouped


def _load_income_allocation_maps(
    *,
    wallet_id: str,
    income_tx_ids: list[str],
    bucket_meta: Dict[str, Dict[str, str]],
) -> tuple[Dict[str, list[Dict[str, Any]]], Dict[str, list[Dict[str, Any]]]]:
    if not income_tx_ids:
        return {}, {}

    tx_ids = tuple(sorted(set(income_tx_ids)))
    tx_bucket_rows: list[Dict[str, Any]] = []
    if frappe.db.exists("DocType", "Hisabi Transaction Bucket"):
        tx_bucket_rows = frappe.db.sql(
            """
            SELECT
                transaction_id,
                bucket_id,
                amount
            FROM `tabHisabi Transaction Bucket`
            WHERE wallet_id=%(wallet_id)s
              AND is_deleted=0
              AND transaction_id IN %(tx_ids)s
            ORDER BY transaction_id ASC, name ASC
            """,
            {"wallet_id": wallet_id, "tx_ids": tx_ids},
            as_dict=True,
        )

    legacy_rows: list[Dict[str, Any]] = []
    if frappe.db.exists("DocType", "Hisabi Transaction Allocation"):
        legacy_rows = frappe.db.sql(
            """
            SELECT
                transaction,
                bucket,
                amount,
                currency
            FROM `tabHisabi Transaction Allocation`
            WHERE wallet_id=%(wallet_id)s
              AND is_deleted=0
              AND transaction IN %(tx_ids)s
            ORDER BY transaction ASC, name ASC
            """,
            {"wallet_id": wallet_id, "tx_ids": tx_ids},
            as_dict=True,
        )

    by_new = _group_allocation_rows(
        tx_bucket_rows,
        tx_key="transaction_id",
        bucket_key="bucket_id",
        amount_key="amount",
        bucket_meta=bucket_meta,
    )
    by_legacy = _group_allocation_rows(
        legacy_rows,
        tx_key="transaction",
        bucket_key="bucket",
        amount_key="amount",
        currency_key="currency",
        bucket_meta=bucket_meta,
    )
    return by_new, by_legacy


def _income_allocations_for_tx(
    *,
    tx_id: str,
    by_new: Dict[str, list[Dict[str, Any]]],
    by_legacy: Dict[str, list[Dict[str, Any]]],
) -> list[Dict[str, Any]]:
    new_rows = by_new.get(tx_id) or []
    if new_rows:
        return new_rows
    return by_legacy.get(tx_id) or []


@frappe.whitelist(allow_guest=False)
def report_summary(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    type: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
        account_id=account_id,
        category_id=category_id,
        type_filter=type,
    )
    tx_rows = _query_transactions(filters=tx_filters, params=params)

    base_currency = _resolve_wallet_base_currency(wallet_id, user)
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    warnings: list[Dict[str, Any]] = []
    warning_seen: set[str] = set()

    total_income = 0.0
    total_expense = 0.0
    for tx in tx_rows:
        tx_type = (tx.get("transaction_type") or "").strip().lower()
        if tx_type not in {"income", "expense"}:
            continue
        amount_base = _tx_amount_in_base(
            tx=tx,
            wallet_id=wallet_id,
            base_currency=base_currency,
            fx_cache=fx_cache,
            warnings=warnings,
            warning_seen=warning_seen,
        )
        if amount_base is None:
            continue
        if tx_type == "income":
            total_income += amount_base
        else:
            total_expense += amount_base

    account_filters = {"wallet_id": wallet_id, "is_deleted": 0}
    normalized_currency = _normalize_currency(currency)
    if normalized_currency:
        account_filters["currency"] = normalized_currency
    accounts = frappe.get_all(
        "Hisabi Account",
        filters=account_filters,
        fields=["name", "account_name", "currency", "current_balance"],
    )
    accounts = [{**row, "account": row.get("name")} for row in accounts]

    budget_filters = {"wallet_id": wallet_id, "is_deleted": 0, "archived": 0}
    if normalized_currency:
        budget_filters["currency"] = normalized_currency
    budgets = frappe.get_all(
        "Hisabi Budget",
        filters=budget_filters,
        fields=["name", "budget_name", "currency", "amount", "spent_amount", "start_date", "end_date"],
    )
    budgets = [{**row, "budget": row.get("name")} for row in budgets]

    goals = frappe.get_all(
        "Hisabi Goal",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=[
            "name",
            "goal_name",
            "goal_type",
            "currency",
            "target_amount",
            "current_amount",
            "progress_percent",
            "remaining_amount",
            "status",
        ],
    )
    goals = [{**row, "goal": row.get("name")} for row in goals]

    debt_totals = frappe.db.sql(
        """
        SELECT direction, COALESCE(SUM(remaining_amount), 0) AS remaining
        FROM `tabHisabi Debt`
        WHERE wallet_id=%s AND is_deleted=0
        GROUP BY direction
        """,
        (wallet_id,),
        as_dict=True,
    )
    debt_summary = {row.direction: row.remaining for row in debt_totals}
    owed_by_me = debt_summary.get("owe", 0) or 0
    owed_to_me = debt_summary.get("owed_to_me", 0) or 0
    debt_summary = {
        **debt_summary,
        "owed_by_me": owed_by_me,
        "owed_to_me": owed_to_me,
        "net": (owed_to_me - owed_by_me),
    }

    upcoming_jameya = frappe.get_all(
        "Hisabi Jameya Payment",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "status": "due"},
        fields=["name", "jameya", "due_date", "amount", "status", "is_my_turn"],
        order_by="due_date asc",
        limit=20,
    )

    return _with_warnings(
        {
            "accounts": accounts,
            "totals": {
                "income": flt(total_income),
                "expense": flt(total_expense),
                "net": flt(total_income - total_expense),
                "total_income": flt(total_income),
                "total_expense": flt(total_expense),
                "base_currency": base_currency,
            },
            "budgets": budgets,
            "goals": goals,
            "debts": debt_summary,
            "jameya_upcoming": upcoming_jameya,
            "from_date": from_date,
            "to_date": to_date,
            "server_time": now_datetime().isoformat(),
        },
        warnings,
    )


@frappe.whitelist(allow_guest=False)
def category_breakdown(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    type: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
        account_id=account_id,
        category_id=category_id,
        type_filter=type,
    )
    tx_rows = _query_transactions(filters=tx_filters, params=params)

    categories_meta = frappe.get_all(
        "Hisabi Category",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "client_id", "category_name", "kind"],
    )
    category_map: Dict[str, Dict[str, Any]] = {}
    for row in categories_meta:
        if row.get("name"):
            category_map[row["name"]] = row
        if row.get("client_id"):
            category_map[row["client_id"]] = row

    base_currency = _resolve_wallet_base_currency(wallet_id, user)
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    warnings: list[Dict[str, Any]] = []
    warning_seen: set[str] = set()

    grouped: Dict[str, Dict[str, Any]] = {}
    total_income = 0.0
    total_expense = 0.0

    for tx in tx_rows:
        tx_type = (tx.get("transaction_type") or "").strip().lower()
        if tx_type not in {"income", "expense"}:
            continue

        converted = _tx_amount_in_base(
            tx=tx,
            wallet_id=wallet_id,
            base_currency=base_currency,
            fx_cache=fx_cache,
            warnings=warnings,
            warning_seen=warning_seen,
        )
        if converted is None:
            continue

        tx_category_id = tx.get("category") or "uncategorized"
        category_info = category_map.get(tx_category_id) or {}
        category_name = category_info.get("category_name") or tx_category_id
        category_kind = category_info.get("kind") or tx_type

        if tx_category_id not in grouped:
            grouped[tx_category_id] = {
                "category_id": tx_category_id,
                "category_name": category_name,
                "kind": category_kind,
                "tx_count": 0,
                "total_income": 0.0,
                "total_expense": 0.0,
            }

        grouped[tx_category_id]["tx_count"] += 1
        if tx_type == "income":
            grouped[tx_category_id]["total_income"] += converted
            total_income += converted
        else:
            grouped[tx_category_id]["total_expense"] += converted
            total_expense += converted

    categories = sorted(
        [
            {
                **row,
                "total_income": flt(row["total_income"]),
                "total_expense": flt(row["total_expense"]),
                "net": flt(row["total_income"] - row["total_expense"]),
            }
            for row in grouped.values()
        ],
        key=lambda row: (-(row["total_income"] + row["total_expense"]), str(row["category_name"])),
    )

    return _with_warnings(
        {
            "categories": categories,
            "totals": {
                "income": flt(total_income),
                "expense": flt(total_expense),
                "net": flt(total_income - total_expense),
                "base_currency": base_currency,
            },
            "from_date": from_date,
            "to_date": to_date,
            "server_time": now_datetime().isoformat(),
        },
        warnings,
    )


@frappe.whitelist(allow_guest=False)
def report_category_breakdown(**kwargs):
    allowed = {
        "from_date",
        "to_date",
        "date_from",
        "date_to",
        "currency",
        "wallet_id",
        "device_id",
        "account_id",
        "category_id",
        "type",
    }
    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    return category_breakdown(**safe_kwargs)


@frappe.whitelist(allow_guest=False)
def cashflow(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    type: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
        account_id=account_id,
        category_id=category_id,
        type_filter=type,
    )
    tx_rows = _query_transactions(filters=tx_filters, params=params)

    base_currency = _resolve_wallet_base_currency(wallet_id, user)
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    warnings: list[Dict[str, Any]] = []
    warning_seen: set[str] = set()

    by_day: Dict[str, Dict[str, float]] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    total_income = 0.0
    total_expense = 0.0

    for tx in tx_rows:
        tx_type = (tx.get("transaction_type") or "").strip().lower()
        if tx_type not in {"income", "expense"}:
            continue

        converted = _tx_amount_in_base(
            tx=tx,
            wallet_id=wallet_id,
            base_currency=base_currency,
            fx_cache=fx_cache,
            warnings=warnings,
            warning_seen=warning_seen,
        )
        if converted is None:
            continue

        day = _parse_date(tx.get("date_time")).isoformat()
        by_day[day][tx_type] += converted
        if tx_type == "income":
            total_income += converted
        else:
            total_expense += converted

    points = []
    for day in sorted(by_day.keys()):
        income_value = flt(by_day[day]["income"])
        expense_value = flt(by_day[day]["expense"])
        points.append(
            {
                "date": day,
                "income": income_value,
                "expense": expense_value,
                "net": flt(income_value - expense_value),
            }
        )

    return _with_warnings(
        {
            "points": points,
            "totals": {
                "income": flt(total_income),
                "expense": flt(total_expense),
                "net": flt(total_income - total_expense),
                "base_currency": base_currency,
            },
            "from_date": from_date,
            "to_date": to_date,
            "server_time": now_datetime().isoformat(),
        },
        warnings,
    )


@frappe.whitelist(allow_guest=False)
def report_cashflow(**kwargs):
    allowed = {
        "from_date",
        "to_date",
        "date_from",
        "date_to",
        "currency",
        "wallet_id",
        "device_id",
        "account_id",
        "category_id",
        "type",
    }
    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    return cashflow(**safe_kwargs)


def _bucket_period_key(tx_date: datetime.date, granularity: str) -> str:
    normalized = (granularity or "daily").strip().lower()
    if normalized == "weekly":
        return (tx_date - datetime.timedelta(days=tx_date.weekday())).isoformat()
    if normalized == "monthly":
        return tx_date.replace(day=1).isoformat()
    return tx_date.isoformat()


def _aggregate_bucket_cashflow(
    *,
    wallet_id: str,
    from_date: Optional[str],
    to_date: Optional[str],
    target_currency: str,
    granularity: str = "daily",
) -> tuple[Dict[tuple[str, str], float], list[Dict[str, Any]]]:
    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        type_filter="income,expense",
    )
    tx_rows = _query_transactions(filters=tx_filters, params=params)

    bucket_meta = _get_wallet_bucket_meta(wallet_id)
    income_tx_ids = [
        str(tx.get("name"))
        for tx in tx_rows
        if (tx.get("transaction_type") or "").strip().lower() == "income" and tx.get("name")
    ]
    by_new, by_legacy = _load_income_allocation_maps(
        wallet_id=wallet_id,
        income_tx_ids=income_tx_ids,
        bucket_meta=bucket_meta,
    )

    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    warnings: list[Dict[str, Any]] = []
    warning_state = {"fx_missing": False}
    points: Dict[tuple[str, str], float] = defaultdict(float)

    for tx in tx_rows:
        tx_type = (tx.get("transaction_type") or "").strip().lower()
        if tx_type not in {"income", "expense"}:
            continue

        tx_name = str(tx.get("name") or "")
        tx_currency = _normalize_currency(tx.get("currency"))
        period_key = _bucket_period_key(_parse_date(tx.get("date_time")), granularity)

        if tx_type == "income":
            alloc_rows = _income_allocations_for_tx(
                tx_id=tx_name,
                by_new=by_new,
                by_legacy=by_legacy,
            )
            for row in alloc_rows:
                converted = _convert_amount_to_currency(
                    amount=row.get("amount"),
                    source_currency=row.get("currency") or tx_currency,
                    target_currency=target_currency,
                    tx_date_time=tx.get("date_time"),
                    wallet_id=wallet_id,
                    fx_cache=fx_cache,
                    warnings=warnings,
                    warning_state=warning_state,
                )
                if converted is None:
                    continue
                points[(period_key, str(row.get("bucket_id")))] += converted
            continue

        # Expense transactions are intentionally grouped under a virtual bucket.
        expense_amount = _convert_amount_to_currency(
            amount=tx.get("amount"),
            source_currency=tx_currency,
            target_currency=target_currency,
            tx_date_time=tx.get("date_time"),
            wallet_id=wallet_id,
            fx_cache=fx_cache,
            warnings=warnings,
            warning_state=warning_state,
        )
        if expense_amount is None:
            continue
        points[(period_key, UNALLOCATED_BUCKET_ID)] -= expense_amount

    return points, warnings


@frappe.whitelist(allow_guest=False)
def report_bucket_breakdown(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    target_currency = _resolve_report_currency(
        wallet_id=wallet_id,
        user=user,
        currency=_resolve_currency_param(currency),
    )
    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        type_filter="income",
    )
    income_rows = _query_transactions(filters=tx_filters, params=params)

    bucket_meta = _get_wallet_bucket_meta(wallet_id)
    income_tx_ids = [str(tx.get("name")) for tx in income_rows if tx.get("name")]
    by_new, by_legacy = _load_income_allocation_maps(
        wallet_id=wallet_id,
        income_tx_ids=income_tx_ids,
        bucket_meta=bucket_meta,
    )

    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    warnings: list[Dict[str, Any]] = []
    warning_state = {"fx_missing": False}
    totals_by_bucket: Dict[str, float] = defaultdict(float)
    total_income = 0.0

    for tx in income_rows:
        tx_name = str(tx.get("name") or "")
        tx_currency = _normalize_currency(tx.get("currency"))
        alloc_rows = _income_allocations_for_tx(
            tx_id=tx_name,
            by_new=by_new,
            by_legacy=by_legacy,
        )
        for row in alloc_rows:
            converted = _convert_amount_to_currency(
                amount=row.get("amount"),
                source_currency=row.get("currency") or tx_currency,
                target_currency=target_currency,
                tx_date_time=tx.get("date_time"),
                wallet_id=wallet_id,
                fx_cache=fx_cache,
                warnings=warnings,
                warning_state=warning_state,
            )
            if converted is None:
                continue
            bucket_id = str(row.get("bucket_id"))
            totals_by_bucket[bucket_id] += converted
            total_income += converted

    data = []
    for bucket_id, amount in totals_by_bucket.items():
        bucket_title = (bucket_meta.get(bucket_id) or {}).get("bucket_title") or bucket_id
        percentage = flt((amount / total_income) * 100, 2) if total_income else 0.0
        data.append(
            {
                "bucket_id": bucket_id,
                "bucket_title": bucket_title,
                "total_amount": flt(amount),
                "percentage_of_income": percentage,
            }
        )

    data.sort(key=lambda row: (-flt(row.get("total_amount") or 0), str(row.get("bucket_id") or "")))
    return {
        "data": data,
        "currency": target_currency,
        "from_date": from_date,
        "to_date": to_date,
        "warnings": warnings,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def report_cashflow_by_bucket(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    target_currency = _resolve_report_currency(
        wallet_id=wallet_id,
        user=user,
        currency=_resolve_currency_param(currency),
    )
    points_map, warnings = _aggregate_bucket_cashflow(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        target_currency=target_currency,
        granularity="daily",
    )

    data = []
    for (day, bucket_id), amount in sorted(points_map.items(), key=lambda item: (item[0][0], item[0][1])):
        data.append(
            {
                "date": day,
                "bucket_id": bucket_id,
                "amount": flt(amount),
            }
        )

    return {
        "data": data,
        "currency": target_currency,
        "from_date": from_date,
        "to_date": to_date,
        "warnings": warnings,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def report_bucket_trends(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
    granularity: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    normalized_granularity = (granularity or "monthly").strip().lower()
    if normalized_granularity not in {"weekly", "monthly"}:
        return _build_invalid_request("granularity must be weekly or monthly", param="granularity")

    target_currency = _resolve_report_currency(
        wallet_id=wallet_id,
        user=user,
        currency=_resolve_currency_param(currency),
    )
    points_map, warnings = _aggregate_bucket_cashflow(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        target_currency=target_currency,
        granularity=normalized_granularity,
    )

    data = []
    for (period_start, bucket_id), amount in sorted(points_map.items(), key=lambda item: (item[0][0], item[0][1])):
        data.append(
            {
                "period_start": period_start,
                "bucket_id": bucket_id,
                "amount": flt(amount),
            }
        )

    return {
        "granularity": normalized_granularity,
        "data": data,
        "currency": target_currency,
        "from_date": from_date,
        "to_date": to_date,
        "warnings": warnings,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def report_trends(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    type: Optional[str] = None,
    granularity: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    # Be explicit: query params may bypass function args in some Frappe call paths.
    request = getattr(frappe.local, "request", None)
    request_granularity = None
    if request and getattr(request, "args", None):
        request_granularity = request.args.get("granularity")
    granularity = (
        granularity
        or frappe.form_dict.get("granularity")
        or get_request_param("granularity")
        or request_granularity
    )
    normalized_granularity = (granularity or "daily").strip().lower()
    if normalized_granularity not in {"daily", "weekly"}:
        return _build_invalid_request("granularity must be daily or weekly", param="granularity")

    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
        account_id=account_id,
        category_id=category_id,
        type_filter=type,
    )
    tx_rows = _query_transactions(filters=tx_filters, params=params)

    base_currency = _resolve_wallet_base_currency(wallet_id, user)
    fx_cache: Dict[tuple[str, str, str, str], Optional[float]] = {}
    warnings: list[Dict[str, Any]] = []
    warning_seen: set[str] = set()

    buckets: Dict[str, Dict[str, float]] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    total_income = 0.0
    total_expense = 0.0

    for tx in tx_rows:
        tx_type = (tx.get("transaction_type") or "").strip().lower()
        if tx_type not in {"income", "expense"}:
            continue

        converted = _tx_amount_in_base(
            tx=tx,
            wallet_id=wallet_id,
            base_currency=base_currency,
            fx_cache=fx_cache,
            warnings=warnings,
            warning_seen=warning_seen,
        )
        if converted is None:
            continue

        tx_date = _parse_date(tx.get("date_time"))
        if normalized_granularity == "weekly":
            period_start = tx_date - datetime.timedelta(days=tx_date.weekday())
            bucket_key = period_start.isoformat()
        else:
            bucket_key = tx_date.isoformat()

        buckets[bucket_key][tx_type] += converted
        if tx_type == "income":
            total_income += converted
        else:
            total_expense += converted

    points = []
    for bucket_key in sorted(buckets.keys()):
        income_value = flt(buckets[bucket_key]["income"])
        expense_value = flt(buckets[bucket_key]["expense"])
        points.append(
            {
                "period_start": bucket_key,
                "income": income_value,
                "expense": expense_value,
                "net": flt(income_value - expense_value),
            }
        )

    return _with_warnings(
        {
            "granularity": normalized_granularity,
            "points": points,
            "totals": {
                "income": flt(total_income),
                "expense": flt(total_expense),
                "net": flt(total_income - total_expense),
                "base_currency": base_currency,
            },
            "from_date": from_date,
            "to_date": to_date,
            "server_time": now_datetime().isoformat(),
        },
        warnings,
    )


@frappe.whitelist(allow_guest=False)
def fx_rates_list(
    wallet_id: Optional[str] = None,
    base_currency: Optional[str] = None,
    quote_currency: Optional[str] = None,
    effective_from: Optional[str] = None,
    effective_to: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    filters: Dict[str, Any] = {
        "wallet_id": wallet_id,
        "is_deleted": 0,
    }
    if _normalize_currency(base_currency):
        filters["base_currency"] = _normalize_currency(base_currency)
    if _normalize_currency(quote_currency):
        filters["quote_currency"] = _normalize_currency(quote_currency)
    if effective_from and effective_to:
        filters["effective_date"] = ["between", [get_datetime(effective_from), get_datetime(effective_to)]]
    elif effective_from:
        filters["effective_date"] = [">=", get_datetime(effective_from)]
    elif effective_to:
        filters["effective_date"] = ["<=", get_datetime(effective_to)]

    rows = frappe.get_all(
        "Hisabi FX Rate",
        filters=filters,
        fields=[
            "name",
            "client_id",
            "wallet_id",
            "base_currency",
            "quote_currency",
            "rate",
            "effective_date",
            "source",
            "doc_version",
            "server_modified",
        ],
        order_by="effective_date desc, server_modified desc, name desc",
    )

    return {
        "rates": rows,
        "count": len(rows),
        "server_time": now_datetime().isoformat(),
        "warnings": [],
    }


@frappe.whitelist(allow_guest=False)
def fx_rates_upsert(
    wallet_id: Optional[str] = None,
    base_currency: Optional[str] = None,
    quote_currency: Optional[str] = None,
    rate: Optional[float] = None,
    effective_date: Optional[str] = None,
    source: Optional[str] = "custom",
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="member")

    base = _normalize_currency(base_currency)
    quote = _normalize_currency(quote_currency)
    if not base:
        return _build_invalid_request("base_currency is required", param="base_currency")
    if not quote:
        return _build_invalid_request("quote_currency is required", param="quote_currency")

    try:
        rate_value = flt(rate)
    except Exception:
        rate_value = 0
    if rate_value <= 0:
        return _build_invalid_request("rate must be greater than zero", param="rate")

    effective_dt = get_datetime(effective_date) if effective_date else now_datetime()
    if not effective_dt:
        return _build_invalid_request("effective_date is invalid", param="effective_date")

    source_value = (source or "custom").strip().lower()
    if source_value not in {"default", "custom", "api"}:
        return _build_invalid_request("source must be one of: default, custom, api", param="source")

    effective_day = effective_dt.date().isoformat()
    existing_name = frappe.db.sql(
        """
        SELECT name
        FROM `tabHisabi FX Rate`
        WHERE wallet_id=%s
          AND is_deleted=0
          AND base_currency=%s
          AND quote_currency=%s
          AND DATE(effective_date)=%s
        ORDER BY effective_date DESC, doc_version DESC, name DESC
        LIMIT 1
        """,
        (wallet_id, base, quote, effective_day),
        as_dict=True,
    )

    if existing_name:
        doc = frappe.get_doc("Hisabi FX Rate", existing_name[0]["name"])
    else:
        doc = frappe.new_doc("Hisabi FX Rate")
        doc.client_id = f"fx-{wallet_id}-{base}-{quote}-{effective_day}"
        doc.name = doc.client_id

    doc.user = user
    doc.wallet_id = wallet_id
    doc.base_currency = base
    doc.quote_currency = quote
    doc.rate = rate_value
    doc.effective_date = effective_dt
    doc.source = source_value
    doc.last_updated = now_datetime()

    apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
    doc.save(ignore_permissions=True)

    return {
        "rate": {
            "name": doc.name,
            "client_id": doc.client_id,
            "wallet_id": doc.wallet_id,
            "base_currency": doc.base_currency,
            "quote_currency": doc.quote_currency,
            "rate": doc.rate,
            "effective_date": doc.effective_date,
            "source": doc.source,
            "doc_version": doc.doc_version,
            "server_modified": doc.server_modified,
        },
        "server_time": now_datetime().isoformat(),
        "warnings": [],
    }


@frappe.whitelist(allow_guest=False)
def report_budgets(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    from_date, to_date = _resolve_date_range(
        from_date=from_date,
        to_date=to_date,
        date_from=date_from,
        date_to=date_to,
    )

    budgets = frappe.get_all(
        "Hisabi Budget",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "archived": 0},
        fields=["name", "budget_name", "currency", "amount", "spent_amount", "start_date", "end_date"],
    )

    result = []
    for budget in budgets:
        if from_date and get_datetime(budget.end_date) < get_datetime(from_date):
            continue
        if to_date and get_datetime(budget.start_date) > get_datetime(to_date):
            continue
        amount = budget.amount or 0
        spent = budget.spent_amount or 0
        percent = (spent / amount * 100) if amount else 0
        result.append(
            {
                **budget,
                "budget": budget.get("name"),
                "remaining": amount - spent,
                "spent_percent": percent,
                "percent": percent,
            }
        )

    return {
        "budgets": result,
        "server_time": now_datetime().isoformat(),
        "warnings": [],
    }


@frappe.whitelist(allow_guest=False)
def report_goals(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    goals = frappe.get_all(
        "Hisabi Goal",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=[
            "name",
            "goal_name",
            "goal_type",
            "currency",
            "target_amount",
            "current_amount",
            "remaining_amount",
            "progress_percent",
            "status",
        ],
    )
    goals = [{**row, "goal": row.get("name")} for row in goals]
    return {
        "goals": goals,
        "server_time": now_datetime().isoformat(),
        "warnings": [],
    }


@frappe.whitelist(allow_guest=False)
def report_debts(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any] | Response:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    debts = frappe.get_all(
        "Hisabi Debt",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=[
            "name",
            "debt_name",
            "direction",
            "currency",
            "principal_amount",
            "remaining_amount",
            "status",
            "confirmed",
            "due_date",
        ],
    )
    totals = {"owed_by_me": 0, "owed_to_me": 0, "net": 0}
    for row in debts:
        if row.get("direction") == "owe":
            totals["owed_by_me"] += row.get("remaining_amount") or 0
        elif row.get("direction") == "owed_to_me":
            totals["owed_to_me"] += row.get("remaining_amount") or 0
    totals["net"] = totals["owed_to_me"] - totals["owed_by_me"]
    return {
        "debts": debts,
        "totals": totals,
        "server_time": now_datetime().isoformat(),
        "warnings": [],
    }
