# SPRINT 07 LOG (Backend)

## Scope
Recurring Transactions v1 backend deliverables on `main`:
- New recurring rule + instance syncable DocTypes
- Deterministic generation API with dry-run/idempotency
- Sync allowlist/type/field integration and wallet-scoped validation
- Recurring gate script and regression tests

## Decision Log
- Rule and instance identities are stable `name == client_id`.
- Generation IDs are deterministic per `(rule_id, occurrence_date)`:
  - Transaction `client_id`: `rtx-<sha1-prefix>`
  - Instance `client_id`: `rinst-<sha1-prefix>`
- `dry_run=1` is strictly read-only: no transaction/instance writes.
- Unsupported transfer generation is explicitly skipped with `transfer_not_supported` warning/reason.
- 422 errors reuse structured `invalid_request` payload pattern.

## Changes
- Added DocType: `Hisabi Recurring Rule`.
- Added DocType: `Hisabi Recurring Instance`.
- Added v1 recurring API module:
  - `rules_list`
  - `upsert_rule`
  - `toggle_rule`
  - `generate`
- Added v1 API wrappers in `api/v1/__init__.py`:
  - `recurring_rules_list`
  - `recurring_rules_upsert`
  - `recurring_rule_toggle`
  - `recurring_generate`
- Wired both recurring doctypes into sync contracts in `api/v1/sync.py`.
- Added wallet/event validator plumbing for recurring doctypes.
- Added backend tests:
  - `test_sync_recurring_rule.py`
  - `test_sync_recurring_instance.py`
  - `test_recurring_generate_api.py`
  - `test_unique_instance_per_date.py`
- Added recurring gate script:
  - `hisabi_backend/hisabi_backend/scripts/verify_recurring.sh`
  - Included in `verify_local_gate_suite.sh`

## Commands Run + Results
1. `bench --site expense.yemenfrappe.com migrate`
   - PASS
2. `bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync_recurring_rule`
   - PASS (`Ran 1 test ... OK`)
3. `bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync_recurring_instance`
   - PASS (`Ran 1 test ... OK`)
4. `bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_recurring_generate_api`
   - PASS (`Ran 2 tests ... OK`)
5. `bench --site expense.yemenfrappe.com run-tests --module hisabi_backend.tests.test_unique_instance_per_date`
   - PASS (`Ran 1 test ... OK`)
6. `BASE_URL=http://expense.yemenfrappe.com:18000 bash hisabi_backend/hisabi_backend/scripts/verify_recurring.sh`
   - PASS (`PASS: verify_recurring`)

## Gate Evidence
- `verify_recurring.sh` checks:
  - token mint
  - account/category seed
  - rule create
  - dry-run generate
  - write generate
  - idempotent rerun (`generated == 0` on rerun)
  - sync pull includes recurring instances + generated recurring tx

## Known Limitations
- `transaction_type=transfer` rule creation is allowed for schema compatibility, but generation currently skips with explicit warning (`transfer_not_supported`).
- Monthly invalid-day occurrences are skipped with warnings (no synthetic date adjustment).
