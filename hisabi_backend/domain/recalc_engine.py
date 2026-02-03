"""Recalculation engine for derived financial fields."""

from __future__ import annotations

from typing import Iterable, Optional

import frappe
from frappe.utils import flt, get_datetime, now_datetime

from hisabi_backend.utils.sync_common import apply_common_sync_fields


def recalc_account_balance(user: str, account_id: str, wallet_id: str | None = None) -> None:
    if not account_id:
        return

    account = frappe.get_doc("Hisabi Account", account_id)
    if wallet_id and getattr(account, "wallet_id", None) and account.wallet_id != wallet_id:
        return
    if not wallet_id and getattr(account, "user", None) and account.user != user:
        return

    where = "wallet_id=%s" if wallet_id else "user=%s"

    outgoing = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(CASE
            WHEN transaction_type = 'income' THEN amount
            WHEN transaction_type = 'expense' THEN -amount
            WHEN transaction_type = 'transfer' THEN -amount
            ELSE 0 END), 0)
        FROM `tabHisabi Transaction`
        WHERE {where} AND is_deleted=0 AND account=%s
        """,
        (wallet_id or user, account_id),
    )[0][0]

    incoming = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(amount), 0)
        FROM `tabHisabi Transaction`
        WHERE {where} AND is_deleted=0 AND to_account=%s
        """,
        (wallet_id or user, account_id),
    )[0][0]

    account.current_balance = flt(account.opening_balance or 0) + flt(outgoing) + flt(incoming)
    apply_common_sync_fields(account, bump_version=True, mark_deleted=False)
    account.save(ignore_permissions=True)


def recalc_budget_spent(user: str, budget_id: str) -> None:
    budget = frappe.get_doc("Hisabi Budget", budget_id)
    if budget.is_deleted:
        return
    if getattr(budget, "wallet_id", None):
        wallet_id = budget.wallet_id
    else:
        wallet_id = None
    if not wallet_id and budget.user != user:
        return

    start_dt = get_datetime(budget.start_date)
    end_dt = get_datetime(budget.end_date)
    if not start_dt or not end_dt:
        return

    conditions = ["is_deleted=0", "transaction_type='expense'"]
    params = {
        "wallet_or_user": wallet_id or user,
        "start": start_dt,
        "end": end_dt,
    }
    if wallet_id:
        conditions.append("wallet_id=%(wallet_or_user)s")
    else:
        conditions.append("user=%(wallet_or_user)s")

    if budget.category:
        conditions.append("category=%(category)s")
        params["category"] = budget.category

    if budget.currency:
        conditions.append("currency=%(currency)s")
        params["currency"] = budget.currency

    conditions.append("date_time >= %(start)s")
    conditions.append("date_time <= %(end)s")

    spent = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(COALESCE(amount_base, amount)), 0)
        FROM `tabHisabi Transaction`
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )[0][0]

    budget.spent_amount = flt(spent)
    apply_common_sync_fields(budget, bump_version=True, mark_deleted=False)
    budget.save(ignore_permissions=True)


def recalc_goal_progress(user: str, goal_id: str) -> None:
    goal = frappe.get_doc("Hisabi Goal", goal_id)
    if goal.is_deleted:
        return
    wallet_id = getattr(goal, "wallet_id", None) or None
    if not wallet_id and goal.user != user:
        return

    target_amount = flt(goal.target_amount or goal.target_amount_base or 0)
    current_amount = 0.0

    if goal.goal_type == "pay_debt" and goal.linked_debt:
        debt = frappe.get_doc("Hisabi Debt", goal.linked_debt)
        if wallet_id and getattr(debt, "wallet_id", None) and debt.wallet_id != wallet_id:
            return
        if not target_amount:
            target_amount = flt(debt.principal_amount or 0)
        current_amount = max(flt(target_amount) - flt(debt.remaining_amount or 0), 0)
    elif goal.linked_account:
        account = frappe.get_doc("Hisabi Account", goal.linked_account)
        if wallet_id and getattr(account, "wallet_id", None) and account.wallet_id != wallet_id:
            return
        current_amount = flt(account.current_balance or 0)

    goal.current_amount = flt(current_amount)
    goal.remaining_amount = max(flt(target_amount) - flt(current_amount), 0)
    goal.progress_percent = (flt(current_amount) / flt(target_amount) * 100) if target_amount else 0

    apply_common_sync_fields(goal, bump_version=True, mark_deleted=False)
    goal.save(ignore_permissions=True)


def recalc_debt_remaining(user: str, debt_id: str) -> None:
    debt = frappe.get_doc("Hisabi Debt", debt_id)
    if debt.is_deleted:
        return
    wallet_id = getattr(debt, "wallet_id", None) or None
    if not wallet_id and debt.user != user:
        return

    paid = frappe.db.sql(
        """
        SELECT COALESCE(SUM(CASE
            WHEN status='paid' THEN COALESCE(paid_amount, amount)
            ELSE 0 END), 0)
        FROM `tabHisabi Debt Installment`
        WHERE debt=%s AND is_deleted=0
        """,
        (debt_id,),
    )[0][0]

    remaining = max(flt(debt.principal_amount or 0) - flt(paid), 0)
    debt.remaining_amount = remaining
    if remaining <= 0:
        debt.status = "closed"
    elif debt.status == "closed":
        debt.status = "active"

    apply_common_sync_fields(debt, bump_version=True, mark_deleted=False)
    debt.save(ignore_permissions=True)


def recalc_jameya_status(user: str, jameya_id: str) -> None:
    jameya = frappe.get_doc("Hisabi Jameya", jameya_id)
    if jameya.is_deleted:
        return
    wallet_id = getattr(jameya, "wallet_id", None) or None
    if not wallet_id and jameya.user != user:
        return

    now = now_datetime()
    payments = frappe.get_all(
        "Hisabi Jameya Payment",
        filters={"jameya": jameya_id, "is_deleted": 0},
        fields=["name", "status", "paid_at", "is_my_turn", "due_date"],
    )

    completed = True
    for payment in payments:
        status = payment.status
        if payment.paid_at and status != "paid" and not payment.is_my_turn:
            status = "paid"
        if payment.is_my_turn and payment.due_date and payment.due_date <= now:
            if status != "received":
                status = "received"
        if status in {"due"}:
            completed = False

        if status != payment.status:
            frappe.db.set_value("Hisabi Jameya Payment", payment.name, "status", status)

    jameya.status = "completed" if completed and payments else jameya.status or "active"
    apply_common_sync_fields(jameya, bump_version=True, mark_deleted=False)
    jameya.save(ignore_permissions=True)


def recalc_budgets(user: str, budget_ids: Optional[Iterable[str]] = None) -> None:
    if budget_ids is None:
        budget_ids = [b.name for b in frappe.get_all("Hisabi Budget", filters={"user": user, "is_deleted": 0})]
    for budget_id in budget_ids:
        recalc_budget_spent(user, budget_id)


def recalc_goals(user: str, goal_ids: Optional[Iterable[str]] = None) -> None:
    if goal_ids is None:
        goal_ids = [g.name for g in frappe.get_all("Hisabi Goal", filters={"user": user, "is_deleted": 0})]
    for goal_id in goal_ids:
        recalc_goal_progress(user, goal_id)


def recalc_debts(user: str, debt_ids: Optional[Iterable[str]] = None) -> None:
    if debt_ids is None:
        debt_ids = [d.name for d in frappe.get_all("Hisabi Debt", filters={"user": user, "is_deleted": 0})]
    for debt_id in debt_ids:
        recalc_debt_remaining(user, debt_id)


def recalc_jameyas(user: str, jameya_ids: Optional[Iterable[str]] = None) -> None:
    if jameya_ids is None:
        jameya_ids = [j.name for j in frappe.get_all("Hisabi Jameya", filters={"user": user, "is_deleted": 0})]
    for jameya_id in jameya_ids:
        recalc_jameya_status(user, jameya_id)
