# Sprint 02 Log

## Backend (`sprint/02-product-core`)

### Commit `d636d84`
- Scope: sync identity + transaction recalc + category E2E verification hardening.
- Why: category `name`/`client_id` drift caused E2E skips; tx account moves needed deterministic dual-account recalculation.
- Files:
  - `hisabi_backend/hisabi_backend/api/v1/sync.py`
  - `hisabi_backend/hisabi_backend/tests/test_sync.py`
  - `hisabi_backend/hisabi_backend/scripts/verify_sync_push_e2e.sh`
- Verification:
  - `bash ./hisabi_backend/scripts/verify_sync_push_e2e.sh` -> PASS
  - `python3 -m compileall hisabi_backend/api/v1/sync.py hisabi_backend/tests/test_sync.py` -> PASS

### Commit `49374b1`
- Scope: reports contract stabilization.
- Why: Sprint 02 requires wallet-scoped `category_breakdown` and `cashflow` with strict 422 validation for missing wallet IDs.
- Files:
  - `hisabi_backend/hisabi_backend/api/v1/reports_finance.py`
  - `hisabi_backend/hisabi_backend/tests/test_budgets_reports.py`
- Verification:
  - `bash ./hisabi_backend/scripts/verify_auth_smoke.sh` -> PASS
  - `bash ./hisabi_backend/scripts/verify_sync_pull.sh` -> PASS
  - `python3 -m compileall hisabi_backend/api/v1/reports_finance.py hisabi_backend/tests/test_budgets_reports.py` -> PASS

### Commit `a762ff6`
- Scope: diag version/commit correctness.
- Why: `/diag` returned stale version and needed runtime source-of-truth + safe commit reporting.
- Files:
  - `hisabi_backend/hisabi_backend/api/v1/health.py`
  - `hisabi_backend/hisabi_backend/tests/test_health_diag.py`
- Verification:
  - `curl -s https://expense.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag` -> PASS (`app.version = v1.0.0-rc.1`)
  - `python3 -m compileall hisabi_backend/api/v1/health.py hisabi_backend/tests/test_health_diag.py` -> PASS

### Commit `814cd23`
- Scope: sync conflict verification gate.
- Why: Sprint 02 needs repeatable validation for conflict payload contract and non-mutating conflict handling.
- Files:
  - `hisabi_backend/hisabi_backend/scripts/verify_sync_conflict_resolution.sh`
- Verification:
  - `bash ./hisabi_backend/scripts/verify_sync_conflict_resolution.sh` -> PASS
  - `bash -n ./hisabi_backend/scripts/verify_sync_conflict_resolution.sh` -> PASS
