"""Scheduled maintenance tasks (Frappe scheduler_events)."""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime

# The idempotency ledger (Hisabi Sync Op) grows with every op ever pushed by every device. Every
# row is terminal (accepted/conflict/error/duplicate/rejected) and is kept only so a replayed
# op_id can be deduped. After this window no client is still reconciling that op, so the rows are
# safe to drop — this bounds the table (query latency + storage) for the life of the product.
SYNC_OP_RETENTION_DAYS = 90
SYNC_OP_DELETE_BATCH = 5000
SYNC_OP_MAX_BATCHES = 400  # safety cap: up to 2M rows per run


def daily() -> None:
    """Frappe `daily` scheduler hook."""
    try:
        prune_sync_ops()
    except Exception:
        frappe.log_error("hisabi_backend.tasks.daily: prune_sync_ops failed", frappe.get_traceback())
    try:
        heal_integrity_drift()
    except Exception:
        frappe.log_error("hisabi_backend.tasks.daily: heal_integrity_drift failed", frappe.get_traceback())


def heal_integrity_drift() -> dict:
    """Re-derive and self-heal drifted budget/debt aggregates fleet-wide (safe derivations only).

    Keeps stored aggregates in step with the transaction ledger so reports stay correct over time.
    Compare-then-write inside the recalc functions means unchanged rows aren't touched (no version
    churn). Account balances and standalone goals are intentionally NOT auto-written — see
    hisabi_backend.domain.integrity.
    """
    from hisabi_backend.domain.integrity import heal_all

    result = heal_all(commit=True)
    if result.get("healed_count") or result.get("reported_only"):
        frappe.logger("hisabi").info(
            f"integrity heal: healed={result.get('healed_count', 0)} "
            f"reported_only={len(result.get('reported_only', []))}"
        )
    return result


def prune_sync_ops(retention_days: int = SYNC_OP_RETENTION_DAYS, *, commit: bool = True) -> int:
    """Delete Hisabi Sync Op ledger rows older than the retention window.

    Batched with per-batch commits (legitimate for a background maintenance job) so no single huge
    lock/transaction is held. Uses the default `creation` index for an efficient ranged delete.
    """
    cutoff = add_days(now_datetime(), -int(retention_days))
    total = 0
    for _ in range(SYNC_OP_MAX_BATCHES):
        frappe.db.sql(
            "DELETE FROM `tabHisabi Sync Op` WHERE creation < %s LIMIT %s",
            (cutoff, SYNC_OP_DELETE_BATCH),
        )
        affected = int(frappe.db.sql("SELECT ROW_COUNT()")[0][0] or 0)
        if commit:
            frappe.db.commit()
        total += affected
        if affected < SYNC_OP_DELETE_BATCH:
            break
    if total:
        frappe.logger("hisabi").info(
            f"prune_sync_ops: removed {total} Hisabi Sync Op rows older than {retention_days}d"
        )
    return total
