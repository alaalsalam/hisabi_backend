# Sprint 04 Log (Backend)

Date: 2026-02-09

## Scope Delivered
- Added and finalized bucket data model for Sprint 04.
- Added `Hisabi Transaction Bucket` with wallet-scoped validation and legacy allocation compatibility.
- Preserved sync invariants and legacy compatibility paths.
- Finalized backend bucket reports with deterministic, wallet-scoped aggregation.

## Backend Reports
- `hisabi_backend.api.v1.reports_finance.report_bucket_breakdown`
- `hisabi_backend.api.v1.reports_finance.report_cashflow_by_bucket`
- `hisabi_backend.api.v1.reports_finance.report_bucket_trends` (weekly/monthly)
- Allocation source order: `Hisabi Transaction Bucket` first, legacy `Hisabi Transaction Allocation` fallback only when no active new rows exist for that transaction.
- Soft-delete respected for transactions, allocations, and buckets.
- FX behavior: no guessed conversion; warning contract used:
  - `{"code":"fx_missing","message":"Some amounts are excluded due to missing FX rates."}`
- Request validation contract:
  - Missing wallet: HTTP 422 `invalid_request` with `param=wallet_id`.
- Cashflow expense policy:
  - Grouped under virtual bucket `unallocated`.

## Commits
- `d738f77` `feat(sync): add transaction bucket model with legacy allocation compatibility`
- `96649fc` `feat(reports): add wallet-scoped bucket finance reports and verification`

## Gates Run (Recorded)
1. `bench --site expense.yemenfrappe.com migrate`
   - Last success lines:
   - `Executing \`after_migrate\` hooks...`
   - `Queued rebuilding of search index for expense.yemenfrappe.com`
2. `bench --site expense.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_bucket_breakdown_report`
   - Last success lines:
   - `Ran 5 tests in 6.336s`
   - `OK`
3. `bench --site expense.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_bucket_cashflow_report`
   - Last success lines:
   - `Ran 5 tests in 4.828s`
   - `OK`
4. `BASE_URL=http://127.0.0.1:18000 bash hisabi_backend/hisabi_backend/scripts/verify_auth_smoke.sh`
   - Last success line:
   - `Smoke test OK: token_len=50 wallet_id=wallet-u-64d6e17bb865`
5. `BASE_URL=http://127.0.0.1:18000 bash hisabi_backend/hisabi_backend/scripts/verify_sync_push_e2e.sh`
   - Last success lines:
   - `Done.`
   - `SUCCESS`
6. `BASE_URL=http://127.0.0.1:18000 bash hisabi_backend/hisabi_backend/scripts/verify_bucket_reports.sh`
   - Last success lines:
   - `breakdown totals OK: 40.00+60.00=100.00`
   - `cashflow totals OK: 40.00+60.00=100.00`
   - `Bucket reports verification OK.`
7. Health diagnostics
   - `curl http://127.0.0.1:18000/health.diag` => `404`
   - `curl http://127.0.0.1:18000/api/method/hisabi_backend.api.v1.health.diag` => `200`
   - Last success payload fragment:
   - `"app":{"name":"hisabi_backend","version":"v1.0.0-rc.2","commit":"e96b1ad"}`

## Gate Closure Evidence (2026-02-09T21:02:12+01:00)

Repo state:
- Branch: `sprint/04-buckets`
- HEAD: `352f1d5d745fde7d4e0d77fa638defbb0927b60d`
- Remotes:
  - `origin https://github.com/alaalsalam/hisabi_backend.git`
  - `upstream https://github.com/alaalsalam/hisabi_backend.git`

Commands and outputs (production target):
1. `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_auth_smoke.sh`
   - Output: `==> Preflight OPTIONS` then exit `code 6`.

2. `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_pull.sh`
   - Output: `==> Register user` then exit `code 6`.

3. `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_push_e2e.sh`
   - Output:
   - `HTTP 000`
   - `Missing token in register response`

4. `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_bucket_reports.sh`
   - Output: `==> Register user` then exit `code 6`.

5. `BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_conflict_resolution.sh`
   - Output: `==> Register user` then exit `code 6`.

6. `curl -sS https://expense.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag | jq .`
   - Output: `curl: (6) Could not resolve host: expense.yemenfrappe.com`

Blocking condition:
- DNS/network egress to `expense.yemenfrappe.com` is unavailable in this execution environment, so production-targeted backend gates could not complete here.
