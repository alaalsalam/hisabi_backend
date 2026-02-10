# Sprint 10 Log (Backend) - Today Center APIs

Date: 2026-02-10  
Branch: `main`

## Scope Delivered
- Added `recurring_due` wrapper endpoint (`GET`) and recurring service method `due(...)`.
- Added `recurring_generate_due` wrapper endpoint (`POST`) and recurring service method `generate_due(...)`.
- Added Sprint 10 backend tests:
  - `hisabi_backend.tests.test_recurring_due_api`
  - `hisabi_backend.tests.test_recurring_generate_due_idempotent`
- Added verifier gate script:
  - `hisabi_backend/hisabi_backend/scripts/verify_today_center.sh`
- Added script to local suite:
  - `hisabi_backend/hisabi_backend/scripts/verify_local_gate_suite.sh`

## Commands Executed
```bash
bench --site expense.yemenfrappe.com migrate
bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_recurring_due_api
bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_recurring_generate_due_idempotent
BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash /home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_today_center.sh
```

## Evidence (PASS)
- `bench migrate`: PASS (exit 0).
- `test_recurring_due_api`: PASS (`Ran 2 tests ... OK`).
- `test_recurring_generate_due_idempotent`: PASS (`Ran 2 tests ... OK`).
- `verify_today_center.sh`: PASS (`TODAY CENTER VERIFY PASS`).
