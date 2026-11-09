"""Data-integrity verification and safe self-healing for derived aggregates.

Every "derived" number in Hisabi (a budget's spent_amount, a debt's remaining_amount, an account's
current_balance) is a function of the immutable transaction/installment ledger. Bugs, partial syncs,
or historical data can leave a stored aggregate out of step with its ledger — the reports then read
wrong even though the underlying operations are intact. This module makes cohesion durable:

  verify_wallet(wallet_id)  — PURE READ. Re-derives every aggregate from the ledger and reports any
                              drift. Never writes.
  heal_wallet(wallet_id)    — Auto-corrects ONLY the aggregates that are pure ledger derivations and
                              therefore always safe to recompute: budget.spent_amount and
                              debt.remaining_amount. It deliberately does NOT rewrite account
                              balances or goal current_amount, because those can encode user-entered
                              starting values (an opening balance stored as a bare current_balance, a
                              standalone savings goal) that a blind recompute would destroy. Those are
                              surfaced by verify_wallet for a human decision instead.
  heal_all(commit)          — Fleet-wide heal used by the daily scheduler.

The verifier reuses the write path's own logic (budget_spent_conditions, compute_account_balance_from
_ledger) so the two can never silently diverge on what counts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import flt

from hisabi_backend.domain.recalc_engine import (
    budget_spent_conditions,
    compute_account_balance_from_ledger,
    recalc_account_balance,
    recalc_budget_spent,
    recalc_debt_remaining,
    _list_account_ledger_entries,
)
from hisabi_backend.utils.sync_common import apply_common_sync_fields

# Money rounds to 2 dp across the app; anything below a cent is noise, not drift.
DRIFT_EPSILON = 0.01


def _expected_budget_spent(budget) -> Optional[float]:
    built = budget_spent_conditions(budget)
    if built is None:
        return None
    conditions, params = built
    spent = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(ABS(COALESCE(amount_base, amount))), 0)
        FROM `tabHisabi Transaction`
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )[0][0]
    return flt(spent)


def _expected_debt_remaining(debt) -> float:
    paid = frappe.db.sql(
        """
        SELECT COALESCE(SUM(CASE
            WHEN status='paid' THEN COALESCE(paid_amount, amount)
            ELSE 0 END), 0)
        FROM `tabHisabi Debt Installment`
        WHERE debt=%s AND is_deleted=0
        """,
        (debt.name,),
    )[0][0]
    return max(flt(debt.principal_amount or 0) - flt(paid), 0)


def _expected_account_balance(account) -> float:
    wallet_id = getattr(account, "wallet_id", None) or None
    ledger = _list_account_ledger_entries(
        account_id=account.name,
        wallet_id=wallet_id,
        user=None if wallet_id else account.user,
    )
    return flt(
        compute_account_balance_from_ledger(
            account_id=account.name,
            opening_balance=flt(account.opening_balance or 0),
            ledger_entries=ledger,
        )
    )


def _drift(entity_type: str, name: str, label: str, stored: float, expected: float, healable: bool) -> Dict[str, Any]:
    return {
        "entity_type": entity_type,
        "name": name,
        "label": label,
        "stored": flt(stored, 2),
        "expected": flt(expected, 2),
        "diff": flt(flt(expected) - flt(stored), 2),
        "healable": healable,
    }


def _wallet_filter(wallet_id: Optional[str]) -> Dict[str, Any]:
    filters: Dict[str, Any] = {"is_deleted": 0}
    if wallet_id:
        filters["wallet_id"] = wallet_id
    return filters


def verify_wallet(wallet_id: Optional[str] = None) -> Dict[str, Any]:
    """Re-derive every aggregate from the ledger and report drift. Read-only."""
    drifts: List[Dict[str, Any]] = []

    for b in frappe.get_all(
        "Hisabi Budget",
        filters=_wallet_filter(wallet_id),
        fields=["name", "budget_name", "spent_amount", "category", "currency",
                "start_date", "end_date", "wallet_id", "user"],
    ):
        expected = _expected_budget_spent(b)
        if expected is None:
            continue
        if abs(flt(b.spent_amount or 0) - expected) > DRIFT_EPSILON:
            drifts.append(_drift("budget", b.name, b.budget_name or b.name,
                                 flt(b.spent_amount or 0), expected, healable=True))

    for d in frappe.get_all(
        "Hisabi Debt",
        filters=_wallet_filter(wallet_id),
        fields=["name", "debt_name", "principal_amount", "remaining_amount", "wallet_id", "user"],
    ):
        expected = _expected_debt_remaining(d)
        if abs(flt(d.remaining_amount or 0) - expected) > DRIFT_EPSILON:
            drifts.append(_drift("debt", d.name, d.debt_name or d.name,
                                 flt(d.remaining_amount or 0), expected, healable=True))

    for a in frappe.get_all(
        "Hisabi Account",
        filters=_wallet_filter(wallet_id),
        fields=["name", "account_name", "opening_balance", "current_balance", "wallet_id", "user"],
    ):
        expected = _expected_account_balance(a)
        if abs(flt(a.current_balance or 0) - expected) > DRIFT_EPSILON:
            # Not auto-healable: a nonzero stored balance with no backing ledger is usually an
            # unrecorded opening balance — zeroing it would delete real money. Report for review.
            drifts.append(_drift("account", a.name, a.account_name or a.name,
                                 flt(a.current_balance or 0), expected, healable=False))

    return {
        "wallet_id": wallet_id,
        "ok": len(drifts) == 0,
        "drift_count": len(drifts),
        "healable_count": sum(1 for x in drifts if x["healable"]),
        "drifts": drifts,
    }


def heal_wallet(wallet_id: Optional[str] = None) -> Dict[str, Any]:
    """Auto-correct only the always-safe pure derivations (budgets, debts). Non-destructive."""
    report = verify_wallet(wallet_id)
    healed: List[Dict[str, Any]] = []
    for drift in report["drifts"]:
        if not drift["healable"]:
            continue
        try:
            if drift["entity_type"] == "budget":
                user = frappe.db.get_value("Hisabi Budget", drift["name"], "user")
                recalc_budget_spent(user, drift["name"])
            elif drift["entity_type"] == "debt":
                user = frappe.db.get_value("Hisabi Debt", drift["name"], "user")
                recalc_debt_remaining(user, drift["name"])
            healed.append(drift)
        except Exception as e:
            frappe.log_error(
                f"integrity heal failed: {drift['entity_type']} {drift['name']}",
                frappe.as_json({"drift": drift, "error": str(e)}),
            )
    return {
        "wallet_id": wallet_id,
        "healed_count": len(healed),
        "healed": healed,
        "reported_only": [d for d in report["drifts"] if not d["healable"]],
    }


def heal_all(commit: bool = False) -> Dict[str, Any]:
    """Fleet-wide safe heal for the daily scheduler. Heals budgets + debts across all wallets."""
    result = heal_wallet(None)
    account_drift = [d for d in result["reported_only"] if d["entity_type"] == "account"]
    if account_drift:
        # Surfaced, never silent: account-balance drift needs a human decision (real opening balance
        # vs phantom) so it is never auto-zeroed — but it MUST show up in the Error Log rather than
        # sitting invisibly, which is how the original drift went unnoticed.
        frappe.log_error(
            "integrity: account balance drift needs review",
            frappe.as_json({"accounts": account_drift}),
        )
    if commit and result["healed_count"]:
        frappe.db.commit()
    return result


def reconcile_account_opening(account_name: str) -> Dict[str, Any]:
    """Adopt an account's unexplained balance as its opening_balance (non-destructive).

    When current_balance cannot be derived from opening_balance + ledger, the unexplained remainder is
    almost always a real starting balance that was never recorded as an opening_balance (or opening
    was later edited away). This folds that remainder INTO opening_balance and re-derives
    current_balance, so the displayed balance is preserved to the cent and the account becomes
    internally consistent. Deliberate, user-confirmed fix only — never call it blindly in a loop, or it
    would mask a genuinely lost transaction as an opening balance.
    """
    acc = frappe.get_doc("Hisabi Account", account_name)
    wallet_id = getattr(acc, "wallet_id", None) or None
    ledger = _list_account_ledger_entries(
        account_id=acc.name, wallet_id=wallet_id, user=None if wallet_id else acc.user
    )
    ledger_delta = compute_account_balance_from_ledger(
        account_id=acc.name, opening_balance=0, ledger_entries=ledger
    )
    new_opening = flt(flt(acc.current_balance or 0) - flt(ledger_delta), 8)
    if flt(acc.opening_balance or 0) != new_opening:
        acc.opening_balance = new_opening
        apply_common_sync_fields(acc, bump_version=True, mark_deleted=False)
        acc.save(ignore_permissions=True)
    recalc_account_balance(acc.user, acc.name, wallet_id=wallet_id)
    return {
        "account": acc.name,
        "opening_balance": new_opening,
        "current_balance": flt(frappe.db.get_value("Hisabi Account", acc.name, "current_balance")),
    }
