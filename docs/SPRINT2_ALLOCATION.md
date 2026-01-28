# Sprint 2 Allocation Engine Rules

## Core Rules
1) Automatic allocation applies only to `transaction_type="income"`.
2) Expense bucket impact:
   - If expense transaction has explicit **Transaction Allocation** rows, those amounts are used for bucket spending.
   - Else if expense has `bucket` set on transaction, treat it as 100% allocated to that bucket.
   - Else ignore for bucket balances.
3) Transaction Allocation rows are derived for auto-allocation and may be hard-deleted/regenerated.
4) Manual allocations are authoritative and must never be overridden by auto allocations.
5) When a transaction is deleted, all allocations (manual or auto) are hard-deleted.

## Rule Resolution (Deterministic)
Priority order for income transactions:
1) Active rule `scope_type="by_account"` where `scope_ref == transaction.account`.
2) Active rule `scope_type="by_income_category"` where `scope_ref == transaction.category`.
3) Active global default (`scope_type="global"` and `is_default=1`).

Tie-breakers: `server_modified` DESC, then `doc_version` DESC.

## Manual Allocation Mode
- `mode="percent"`: percent must be 1..100 and total <= 100.
- `mode="amount"`: sum of amounts <= transaction amount, percent is stored as rounded integer.
- Remainders (from rounding) are applied to the highest-percent allocation.

## Idempotency
Auto allocations are hard-deleted and recreated for income transactions when no manual overrides exist. This guarantees deterministic results across repeated sync calls.
