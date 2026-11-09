"""Recalculation engine for derived financial fields."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

import frappe
from frappe.utils import flt, get_datetime, now_datetime

from hisabi_backend.utils.sync_common import apply_common_sync_fields


def _list_account_ledger_entries(
    *,
    account_id: str,
    wallet_id: str | None = None,
    user: str | None = None,
) -> list[dict[str, Any]]:
    """Return wallet/user-scoped transaction ledger rows touching this account.

    Invariant: ordering must be deterministic so the same ledger always yields
    the same computed balance regardless of query pagination or retries.
    """
    if not account_id:
        return []
    if not wallet_id and not user:
        return []

    scope_field = "wallet_id" if wallet_id else "user"
    scope_value = wallet_id or user
    has_converted_amount = frappe.db.has_column("Hisabi Transaction", "converted_amount")
    converted_expr = "converted_amount" if has_converted_amount else "NULL AS converted_amount"
    # LOCK IN SHARE MODE makes this a locking read, so it returns the LATEST COMMITTED ledger
    # rows instead of this transaction's (possibly stale) REPEATABLE-READ snapshot. This is
    # essential: the caller holds a FOR UPDATE lock on the account row for serialization, but a
    # plain SELECT here would still read the snapshot and miss a concurrently-committed row —
    # producing a silently wrong balance. Shared (not exclusive) locks stay compatible across
    # concurrent recalcs that share a transfer row, avoiding deadlocks.
    return frappe.db.sql(
        f"""
        SELECT name, transaction_type, amount, {converted_expr}, account, to_account, date_time
        FROM `tabHisabi Transaction`
        WHERE {scope_field}=%s
          AND is_deleted=0
          AND (account=%s OR to_account=%s)
        ORDER BY COALESCE(date_time, creation) ASC, name ASC
        LOCK IN SHARE MODE
        """,
        (scope_value, account_id, account_id),
        as_dict=True,
    )


def _ledger_delta_for_account(entry: Mapping[str, Any], account_id: str) -> float:
    """Pure delta calculator for a single ledger entry."""
    if not account_id:
        return 0.0

    tx_type = (entry.get("transaction_type") or "").strip().lower()
    source_account = entry.get("account")
    target_account = entry.get("to_account")
    amount = flt(entry.get("amount") or 0)
    converted_amount = flt(entry.get("converted_amount") or 0)
    if not amount and not converted_amount:
        return 0.0

    source_leg_amount = converted_amount or amount
    if tx_type == "transfer":
        # Transfers store the source leg in `amount` and destination leg in `converted_amount`.
        source_leg_amount = amount
    target_leg_amount = converted_amount or amount

    delta = 0.0
    if source_account == account_id:
        if tx_type == "income":
            delta += source_leg_amount
        elif tx_type in {"expense", "transfer"}:
            delta -= source_leg_amount
    if target_account == account_id and tx_type == "transfer":
        # Transfer incoming leg increases the destination account balance.
        delta += target_leg_amount
    return flt(delta)


def compute_account_balance_from_ledger(
    *,
    account_id: str,
    opening_balance: float,
    ledger_entries: Iterable[Mapping[str, Any]],
) -> float:
    """Pure function: compute account balance from opening balance + ledger rows."""
    balance = flt(opening_balance or 0)
    for entry in ledger_entries:
        balance += _ledger_delta_for_account(entry, account_id)
    return flt(balance)


def recalc_account_balance(user: str, account_id: str, wallet_id: str | None = None) -> None:
    if not account_id:
        return

    # Serialize concurrent recalcs of THIS account across devices. FOR UPDATE is a locking read:
    # (a) a second concurrent sync_push recomputing the same account blocks until we commit, and
    # (b) it returns the LATEST COMMITTED row values (opening/current balance), bypassing this
    # transaction's stale REPEATABLE-READ snapshot. Without this, two offline devices posting to
    # the same shared account could each persist a balance missing the other's transaction.
    locked = frappe.db.sql(
        "SELECT opening_balance, current_balance, wallet_id, `user` "
        "FROM `tabHisabi Account` WHERE name=%s FOR UPDATE",
        (account_id,),
        as_dict=True,
    )
    if not locked:
        return
    locked = locked[0]
    if wallet_id and locked.get("wallet_id") and locked.get("wallet_id") != wallet_id:
        return
    if not wallet_id and locked.get("user") and locked.get("user") != user:
        return

    effective_wallet_id = wallet_id or (locked.get("wallet_id") or None)
    ledger_rows = _list_account_ledger_entries(
        account_id=account_id,
        wallet_id=effective_wallet_id,
        user=None if effective_wallet_id else user,
    )
    new_balance = compute_account_balance_from_ledger(
        account_id=account_id,
        opening_balance=flt(locked.get("opening_balance") or 0),
        ledger_entries=ledger_rows,
    )
    # Only persist (and bump doc_version/server_modified) when the balance actually changed. An
    # unconditional save on every push generates spurious pull payloads for this account and can
    # trigger a false base_version conflict for another device holding a pending edit to it.
    if flt(new_balance) == flt(locked.get("current_balance") or 0):
        return
    account = frappe.get_doc("Hisabi Account", account_id)
    account.current_balance = new_balance
    apply_common_sync_fields(account, bump_version=True, mark_deleted=False)
    account.save(ignore_permissions=True)


def budget_spent_conditions(budget) -> Optional[tuple[list[str], dict]]:
    """Single source of truth for the WHERE that defines a budget's spend.

    Returns (conditions, params) or None when the budget lacks a valid date window. Shared by the
    write path (recalc_budget_spent, with a lock) and the read-only integrity verifier, so the two
    can never silently diverge on which transactions count toward a budget.

    amount_base is SIGNED (negative for expense); the caller aggregates ABS(...) because "spent" is
    a positive magnitude — summing the raw signed value produced a negative spent_amount that broke
    budget progress (spent/limit) and remaining (limit-spent) on every consumer.
    """
    start_dt = get_datetime(budget.start_date)
    end_dt = get_datetime(budget.end_date)
    if not start_dt or not end_dt:
        return None

    wallet_id = getattr(budget, "wallet_id", None) or None
    conditions = ["is_deleted=0", "transaction_type='expense'"]
    params = {
        "wallet_or_user": wallet_id or budget.user,
        "start": start_dt,
        "end": end_dt,
    }
    conditions.append("wallet_id=%(wallet_or_user)s" if wallet_id else "user=%(wallet_or_user)s")

    if budget.category:
        conditions.append("category=%(category)s")
        params["category"] = budget.category

    if budget.currency:
        conditions.append("currency=%(currency)s")
        params["currency"] = budget.currency

    conditions.append("date_time >= %(start)s")
    conditions.append("date_time <= %(end)s")
    return conditions, params


def recalc_budget_spent(user: str, budget_id: str) -> None:
    if not budget_id:
        return
    # Serialize concurrent recalcs of this budget (see recalc_account_balance for rationale).
    if not frappe.db.sql("SELECT name FROM `tabHisabi Budget` WHERE name=%s FOR UPDATE", (budget_id,)):
        return
    budget = frappe.get_doc("Hisabi Budget", budget_id)
    if budget.is_deleted:
        return
    wallet_id = getattr(budget, "wallet_id", None) or None
    if not wallet_id and budget.user != user:
        return

    built = budget_spent_conditions(budget)
    if built is None:
        return
    conditions, params = built

    spent = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(ABS(COALESCE(amount_base, amount))), 0)
        FROM `tabHisabi Transaction`
        WHERE {' AND '.join(conditions)}
        LOCK IN SHARE MODE
        """,
        params,
    )[0][0]

    # Compare-then-write: skip the save (and doc_version bump) when nothing changed, so recalcs on
    # every push — and the daily integrity heal — don't churn versions and trigger false conflicts.
    if flt(spent) == flt(budget.spent_amount or 0):
        return
    budget.spent_amount = flt(spent)
    apply_common_sync_fields(budget, bump_version=True, mark_deleted=False)
    budget.save(ignore_permissions=True)


def recalc_goal_progress(user: str, goal_id: str) -> None:
    if not goal_id:
        return
    # Serialize concurrent recalcs of this goal (see recalc_account_balance for rationale).
    if not frappe.db.sql("SELECT name FROM `tabHisabi Goal` WHERE name=%s FOR UPDATE", (goal_id,)):
        return
    goal = frappe.get_doc("Hisabi Goal", goal_id)
    if goal.is_deleted:
        return
    wallet_id = getattr(goal, "wallet_id", None) or None
    if not wallet_id and goal.user != user:
        return

    target_amount = flt(goal.target_amount or goal.target_amount_base or 0)
    # Standalone "save" goals track a user-entered saved amount — preserve it. Only linked goals
    # (pay_debt / linked_account) derive current_amount from their source below.
    current_amount = flt(goal.current_amount or 0)

    if goal.goal_type == "pay_debt" and goal.linked_debt:
        # Locking read → fresh committed remaining_amount, not this txn's stale snapshot.
        drow = frappe.db.sql(
            "SELECT wallet_id, principal_amount, remaining_amount FROM `tabHisabi Debt` "
            "WHERE name=%s LOCK IN SHARE MODE",
            (goal.linked_debt,),
            as_dict=True,
        )
        if not drow:
            return
        debt = drow[0]
        if wallet_id and debt.get("wallet_id") and debt.get("wallet_id") != wallet_id:
            return
        if not target_amount:
            target_amount = flt(debt.get("principal_amount") or 0)
        current_amount = max(flt(target_amount) - flt(debt.get("remaining_amount") or 0), 0)
    elif goal.linked_account:
        arow = frappe.db.sql(
            "SELECT wallet_id, current_balance FROM `tabHisabi Account` "
            "WHERE name=%s LOCK IN SHARE MODE",
            (goal.linked_account,),
            as_dict=True,
        )
        if not arow:
            return
        account = arow[0]
        if wallet_id and account.get("wallet_id") and account.get("wallet_id") != wallet_id:
            return
        current_amount = flt(account.get("current_balance") or 0)

    goal.current_amount = flt(current_amount)
    goal.remaining_amount = max(flt(target_amount) - flt(current_amount), 0)
    goal.progress_percent = (flt(current_amount) / flt(target_amount) * 100) if target_amount else 0

    apply_common_sync_fields(goal, bump_version=True, mark_deleted=False)
    goal.save(ignore_permissions=True)


def recalc_debt_remaining(user: str, debt_id: str) -> None:
    if not debt_id:
        return
    # Serialize concurrent recalcs of this debt (see recalc_account_balance for rationale).
    if not frappe.db.sql("SELECT name FROM `tabHisabi Debt` WHERE name=%s FOR UPDATE", (debt_id,)):
        return
    debt = frappe.get_doc("Hisabi Debt", debt_id)
    if debt.is_deleted:
        return
    wallet_id = getattr(debt, "wallet_id", None) or None
    if not wallet_id and debt.user != user:
        return

    row = frappe.db.sql(
        """
        SELECT COUNT(*) AS cnt,
               COALESCE(SUM(CASE WHEN status='paid' THEN COALESCE(paid_amount, amount) ELSE 0 END), 0) AS paid
        FROM `tabHisabi Debt Installment`
        WHERE debt=%s AND is_deleted=0
        LOCK IN SHARE MODE
        """,
        (debt_id,),
        as_dict=True,
    )[0]

    principal = flt(debt.principal_amount or 0)
    if int(row.cnt or 0) > 0:
        # Installment debt: remaining is derived from the sum of paid installments.
        remaining = max(principal - flt(row.paid), 0)
    else:
        # Simple debt: remaining is client-managed (direct repayments, or partially-paid on create).
        # Preserve the stored value (clamped) instead of forcing it back to the full principal.
        stored = debt.remaining_amount
        remaining = flt(stored) if stored is not None else principal
        remaining = max(remaining, 0)
        if principal > 0:
            remaining = min(remaining, principal)
    debt.remaining_amount = remaining
    if remaining <= 0:
        debt.status = "closed"
    elif debt.status == "closed":
        debt.status = "active"

    apply_common_sync_fields(debt, bump_version=True, mark_deleted=False)
    debt.save(ignore_permissions=True)


def recalc_jameya_status(user: str, jameya_id: str) -> None:
    if not jameya_id:
        return
    # Serialize concurrent recalcs of this jameya (see recalc_account_balance for rationale).
    if not frappe.db.sql("SELECT name FROM `tabHisabi Jameya` WHERE name=%s FOR UPDATE", (jameya_id,)):
        return
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


# All fan-out helpers below iterate in sorted() order so that two concurrent sync_push
# transactions acquire the per-entity FOR UPDATE locks in the SAME order — this removes the
# lock-ordering deadlock class (A locks X then Y while B locks Y then X).

def recalc_budgets(user: str, budget_ids: Optional[Iterable[str]] = None) -> None:
    if budget_ids is None:
        budget_ids = [b.name for b in frappe.get_all("Hisabi Budget", filters={"user": user, "is_deleted": 0})]
    for budget_id in sorted(budget_ids):
        recalc_budget_spent(user, budget_id)


def recalc_goals(user: str, goal_ids: Optional[Iterable[str]] = None) -> None:
    if goal_ids is None:
        goal_ids = [g.name for g in frappe.get_all("Hisabi Goal", filters={"user": user, "is_deleted": 0})]
    for goal_id in sorted(goal_ids):
        recalc_goal_progress(user, goal_id)


def recalc_debts(user: str, debt_ids: Optional[Iterable[str]] = None) -> None:
    if debt_ids is None:
        debt_ids = [d.name for d in frappe.get_all("Hisabi Debt", filters={"user": user, "is_deleted": 0})]
    for debt_id in sorted(debt_ids):
        recalc_debt_remaining(user, debt_id)


def recalc_jameyas(user: str, jameya_ids: Optional[Iterable[str]] = None) -> None:
    if jameya_ids is None:
        jameya_ids = [j.name for j in frappe.get_all("Hisabi Jameya", filters={"user": user, "is_deleted": 0})]
    for jameya_id in sorted(jameya_ids):
        recalc_jameya_status(user, jameya_id)
