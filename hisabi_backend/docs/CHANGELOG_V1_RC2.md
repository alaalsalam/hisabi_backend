# CHANGELOG_V1_RC2

Release: `v1.0.0-rc.2`
Date: `2026-02-09`

## Highlights
- Sync identity hardening for client-generated docs:
  - Stable `name` + `client_id` contract on pull/push for categories/accounts.
  - Category E2E no longer skips due to `name != client_id` drift.
- Transaction correctness:
  - Deterministic account balance recalculation when transaction account links change.
  - Idempotent replay checks preserved for create/update/delete operations.
- Reports V1 expansion:
  - Added wallet-scoped `category_breakdown` (date-range aware).
  - Added wallet-scoped `cashflow` (date-range aware).
  - Missing/invalid `wallet_id` consistently returns `422 invalid_request`.
- Diagnostics correctness:
  - `diag` version sourced from `hisabi_backend.__version__`.
  - `diag` commit sourced from runtime env/git safely.
- Verification gates:
  - Added `verify_sync_conflict_resolution.sh` to validate conflict payload contract and non-mutating conflict behavior.

## Compatibility
- No menu/route removals.
- No contract-breaking changes for existing sync entities.
- Wallet scoping invariants preserved.
