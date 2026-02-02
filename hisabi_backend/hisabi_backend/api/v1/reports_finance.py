"""Financial reporting APIs (v1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe.utils import get_datetime, now_datetime

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.request_params import get_request_param
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


@frappe.whitelist(allow_guest=False)
def report_summary(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    # Be explicit: Frappe RPC sometimes doesn't populate `wallet_id` into function args.
    # Read from form_dict and then fall back to request parsing helper (args/query_string/json body).
    wallet_id = wallet_id or frappe.form_dict.get("wallet_id") or get_request_param("wallet_id")
    if not wallet_id:
        frappe.throw("wallet_id is required", frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    params: Dict[str, Any] = {"wallet_id": wallet_id}
    date_filters = ["wallet_id=%(wallet_id)s", "is_deleted=0"]
    if from_date:
        params["from_date"] = get_datetime(from_date)
        date_filters.append("date_time >= %(from_date)s")
    if to_date:
        params["to_date"] = get_datetime(to_date)
        date_filters.append("date_time <= %(to_date)s")
    if currency:
        params["currency"] = currency
        date_filters.append("currency = %(currency)s")

    totals = frappe.db.sql(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END), 0) AS total_income,
            COALESCE(SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END), 0) AS total_expense
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
def report_budgets(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = wallet_id or get_request_param("wallet_id")
    if not wallet_id:
        frappe.throw("wallet_id is required", frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
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
    wallet_id = wallet_id or get_request_param("wallet_id")
    if not wallet_id:
        frappe.throw("wallet_id is required", frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
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
    wallet_id = wallet_id or get_request_param("wallet_id")
    if not wallet_id:
        frappe.throw("wallet_id is required", frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
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
