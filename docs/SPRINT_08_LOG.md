# Sprint 08 Log (Backend)

Date: 2026-02-10
Branch: `main`

## Scope Delivered
- Rule edit safety API: `recurring.apply_changes` with `future_only` and `rebuild_scheduled`.
- Instance skip API: `recurring.skip_instance` with `tx_exists` warning contract.
- Rule pause API: `recurring.pause_until` + new rule field `resume_date`.
- Deterministic preview API: `recurring.preview` with `would_create` and invalid-day warnings.
- Generation policy hardened: scheduled instances are consumed/updated, skipped instances are not recreated, inactive+resume window respected.
- Recurring coverage report: `reports_finance.report_recurring_coverage`.
- Sync allowlist/maps updated for additive recurring field: `resume_date`.
- New backend tests:
  - `test_recurring_rule_edit_policy.py`
  - `test_recurring_instance_skip.py`
  - `test_recurring_preview_api.py`
- Gate script upgraded: `hisabi_backend/scripts/verify_recurring.sh` now validates payload edit/rebuild/skip/no-duplicate behavior.

## Commands Executed
```bash
bench --site expense.yemenfrappe.com migrate
bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_recurring_rule_edit_policy
bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_recurring_instance_skip
bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_recurring_preview_api
BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_recurring.sh
python -m compileall hisabi_backend/hisabi_backend/api/v1/recurring.py hisabi_backend/hisabi_backend/api/v1/reports_finance.py hisabi_backend/hisabi_backend/api/v1/__init__.py hisabi_backend/hisabi_backend/doctype/hisabi_recurring_rule/hisabi_recurring_rule.py hisabi_backend/hisabi_backend/tests/test_recurring_rule_edit_policy.py hisabi_backend/hisabi_backend/tests/test_recurring_instance_skip.py hisabi_backend/hisabi_backend/tests/test_recurring_preview_api.py
```

## Evidence (Pass)
- Migrate: PASS (`expense.yemenfrappe.com`).
- Tests: 3/3 modules PASS.
- Verify recurring gate: PASS (`PASS: verify_recurring`).
- Compileall: PASS (exit 0).

## Notes
- New recurring validation errors use strict `422` object shape with codes:
  - `RECURRING_VALIDATION_ERROR`
  - `RECURRING_CONFLICT`
  - `RECURRING_INSTANCE_EXISTS`
  - `RECURRING_TX_EXISTS`
- Existing generated transactions are intentionally not retro-edited on rule updates.
