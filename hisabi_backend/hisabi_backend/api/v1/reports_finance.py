"""Financial reporting APIs (v1)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import frappe
from frappe.utils import get_datetime, now_datetime
from werkzeug.wrappers import Response

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.request_params import get_request_param
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


def _build_tx_filters(
    *,
    wallet_id: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
) -> tuple[list[str], Dict[str, Any]]:
    params: Dict[str, Any] = {"wallet_id": wallet_id}
    filters = ["wallet_id=%(wallet_id)s", "is_deleted=0"]
    if from_date:
        params["from_date"] = get_datetime(from_date)
        filters.append("date_time >= %(from_date)s")
    if to_date:
        params["to_date"] = get_datetime(to_date)
        filters.append("date_time <= %(to_date)s")
    if currency:
        params["currency"] = currency
        filters.append("currency = %(currency)s")
    return filters, params


@frappe.whitelist(allow_guest=False)
def report_summary(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    # Reporting must exclude soft-deleted rows unless explicitly requested.
    date_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
    )

    totals = frappe.db.sql(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type='income' THEN COALESCE(amount_base, amount) ELSE 0 END), 0) AS total_income,
            COALESCE(SUM(CASE WHEN transaction_type='expense' THEN COALESCE(amount_base, amount) ELSE 0 END), 0) AS total_expense
        FROM `tabHisabi Transaction`
        WHERE {' AND '.join(date_filters)}
        """,
        params,
        as_dict=True,
    )[0]

    account_filters = {"wallet_id": wallet_id, "is_deleted": 0}
    if currency:
        account_filters["currency"] = currency
    accounts = frappe.get_all(
        "Hisabi Account",
        filters=account_filters,
        fields=["name", "account_name", "currency", "current_balance"],
    )
    accounts = [
        {**row, "account": row.get("name")}
        for row in accounts
    ]

    budget_filters = {"wallet_id": wallet_id, "is_deleted": 0, "archived": 0}
    if currency:
        budget_filters["currency"] = currency
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

    total_income = totals.get("total_income", 0) if totals else 0
    total_expense = totals.get("total_expense", 0) if totals else 0
    totals_out = {
        "income": total_income,
        "expense": total_expense,
        "net": total_income - total_expense,
        "total_income": total_income,
        "total_expense": total_expense,
    }

    return {
        "accounts": accounts,
        "totals": totals_out,
        "budgets": budgets,
        "goals": goals,
        "debts": debt_summary,
        "jameya_upcoming": upcoming_jameya,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def category_breakdown(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
    )
    tx_filters.append("transaction_type IN ('income', 'expense')")

    rows = frappe.db.sql(
        f"""
        SELECT
            tx.category AS category_id,
            COALESCE(cat.category_name, tx.category, 'uncategorized') AS category_name,
            COALESCE(cat.kind, tx.transaction_type) AS kind,
            COUNT(*) AS tx_count,
            COALESCE(SUM(CASE
                WHEN tx.transaction_type='income' THEN COALESCE(tx.amount_base, tx.amount)
                ELSE 0
            END), 0) AS total_income,
            COALESCE(SUM(CASE
                WHEN tx.transaction_type='expense' THEN COALESCE(tx.amount_base, tx.amount)
                ELSE 0
            END), 0) AS total_expense
        FROM `tabHisabi Transaction` tx
        LEFT JOIN `tabHisabi Category` cat
            ON (cat.name = tx.category OR cat.client_id = tx.category)
            AND cat.wallet_id = tx.wallet_id
            AND cat.is_deleted = 0
        WHERE {' AND '.join(f'tx.{flt}' for flt in tx_filters)}
        GROUP BY tx.category, COALESCE(cat.category_name, tx.category, 'uncategorized'), COALESCE(cat.kind, tx.transaction_type)
        ORDER BY
            (
                COALESCE(SUM(CASE
                    WHEN tx.transaction_type='income' THEN COALESCE(tx.amount_base, tx.amount)
                    ELSE 0
                END), 0)
                +
                COALESCE(SUM(CASE
                    WHEN tx.transaction_type='expense' THEN COALESCE(tx.amount_base, tx.amount)
                    ELSE 0
                END), 0)
            ) DESC,
            category_name ASC
        """,
        params,
        as_dict=True,
    )

    categories = []
    total_income = 0
    total_expense = 0
    for row in rows:
        income_amount = row.get("total_income") or 0
        expense_amount = row.get("total_expense") or 0
        total_income += income_amount
        total_expense += expense_amount
        category_id = row.get("category_id") or "uncategorized"
        categories.append(
            {
                "category_id": category_id,
                "category_name": row.get("category_name") or category_id,
                "kind": row.get("kind") or "expense",
                "tx_count": row.get("tx_count") or 0,
                "total_income": income_amount,
                "total_expense": expense_amount,
                "net": income_amount - expense_amount,
            }
        )

    return {
        "categories": categories,
        "totals": {
            "income": total_income,
            "expense": total_expense,
            "net": total_income - total_expense,
        },
        "from_date": from_date,
        "to_date": to_date,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def cashflow(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

    tx_filters, params = _build_tx_filters(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        currency=currency,
    )
    tx_filters.append("transaction_type IN ('income', 'expense')")

    rows = frappe.db.sql(
        f"""
        SELECT
            DATE(date_time) AS day,
            COALESCE(SUM(CASE
                WHEN transaction_type='income' THEN COALESCE(amount_base, amount)
                ELSE 0
            END), 0) AS income,
            COALESCE(SUM(CASE
                WHEN transaction_type='expense' THEN COALESCE(amount_base, amount)
                ELSE 0
            END), 0) AS expense
        FROM `tabHisabi Transaction`
        WHERE {' AND '.join(tx_filters)}
        GROUP BY DATE(date_time)
        ORDER BY DATE(date_time) ASC
        """,
        params,
        as_dict=True,
    )

    points = []
    total_income = 0
    total_expense = 0
    for row in rows:
        income_amount = row.get("income") or 0
        expense_amount = row.get("expense") or 0
        total_income += income_amount
        total_expense += expense_amount
        points.append(
            {
                "date": str(row.get("day")),
                "income": income_amount,
                "expense": expense_amount,
                "net": income_amount - expense_amount,
            }
        )

    return {
        "points": points,
        "totals": {
            "income": total_income,
            "expense": total_expense,
            "net": total_income - total_expense,
        },
        "from_date": from_date,
        "to_date": to_date,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def report_budgets(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id_resolved = _resolve_wallet_id_param(wallet_id)
    if isinstance(wallet_id_resolved, Response):
        return wallet_id_resolved
    wallet_id = wallet_id_resolved
    require_wallet_member(wallet_id, user, min_role="viewer")

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

    return {"budgets": result, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def report_goals(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
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
    return {"goals": goals, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def report_debts(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
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
    return {"debts": debts, "totals": totals, "server_time": now_datetime().isoformat()}
