# Sprint 03 Log (Backend)

Date: 2026-02-09
Repo: `hisabi_backend`
Branch: `sprint/02-product-core`
Target environment: `https://expense.yemenfrappe.com`

## Commit Units (What / Why / Verification)

### `9fc3041` feat(balance): add deterministic ledger recompute utility with coverage
What:
- Added deterministic ledger recompute utility in `domain/recalc_engine.py`.
- Added backend tests for create/update/delete/cross-account transaction balance behavior.

Why:
- Sprint 03 requires wallet-scoped deterministic balance recalculation under edits/deletes/concurrency.

Verification command:
- `bench --site expense.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_transaction_balance_determinism`

Last success line:
- `Ran 5 tests in 5.711s`
- `OK`

### `c904a6d` feat(reports): add FX-backed report contract with trends and filters
What:
- Added FX endpoints and report contract expansion in `hisabi_backend/api/v1/reports_finance.py`.
- Added `report_trends` + filter support + warnings array behavior.
- Added tests for wallet_id validation, FX warning contract, and trends/filter contract.

Why:
- Sprint 03 requires stable wallet-scoped report contracts with explicit FX-missing warnings (no fake conversion).

Verification command:
- `bench --site expense.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_reports_contract_v2`

Last success line:
- `Ran 3 tests in 4.570s`
- `OK`

### `757a109` test(gates): add reports contract verifier and script safety header
What:
- Added `hisabi_backend/scripts/verify_reports_contract.sh`.
- Added WHY/WHEN/SAFETY header to conflict verifier script.

Why:
- Sprint 03 requires a release gate that validates reports contract + 422 invalid_request behavior.

Verification commands:
- `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash hisabi_backend/scripts/verify_reports_contract.sh`
- `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash hisabi_backend/scripts/verify_sync_conflict_resolution.sh`

Last success line:
- `Reports contract verification OK.`
- `Conflict resolution contract OK.`

### `cdf7799` fix(diag): resolve runtime version from app root deterministically
What:
- Updated diag version resolution in `hisabi_backend/api/v1/health.py` to read app-root `__version__` reliably.

Why:
- Sprint 03 requires diag consistency with runtime release version and commit hash.

Verification command:
- `curl -sS -o /tmp/hisabi_diag_body.json -w '%{http_code}' https://expense.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag`
- `jq . /tmp/hisabi_diag_body.json`

Last success line:
- HTTP status: `200`
- `"version": "v1.0.0-rc.2"`
- `"commit": "cdf7799"`

## Sprint 03 Backend Gates (Executed)

Environment:
- `BASE_URL=https://expense.yemenfrappe.com`
- `ORIGIN=http://localhost:8082`

Commands and terminal outcome:
- `bash hisabi_backend/scripts/verify_auth_smoke.sh`
  - `Smoke test OK: token_len=50 wallet_id=wallet-u-0cf50107471d`
- `bash hisabi_backend/scripts/verify_sync_pull.sh`
  - `Done.`
- `bash hisabi_backend/scripts/verify_sync_push_e2e.sh`
  - `SUCCESS`
- `bash hisabi_backend/scripts/verify_sync_conflict_resolution.sh`
  - `Conflict resolution contract OK.`
- `bash hisabi_backend/scripts/verify_reports_contract.sh`
  - `Reports contract verification OK.`
- `curl https://expense.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag`
  - HTTP `200`, app version `v1.0.0-rc.2`, commit `cdf7799`

Raw gate logs captured at:
- `/tmp/hisabi_verify_auth_smoke.log`
- `/tmp/hisabi_verify_sync_pull.log`
- `/tmp/hisabi_verify_sync_push_e2e.log`
- `/tmp/hisabi_verify_sync_conflict_resolution.log`
- `/tmp/hisabi_verify_reports_contract.log`
