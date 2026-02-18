# PR: Sprint 03 Product Core (Backend)

## Scope
- Deterministic transaction impact and account balance recomputation from ledger.
- Reports v2 contract stabilization with wallet-scoped filters and warnings:
  - `report_summary`
  - `report_cashflow`
  - `report_category_breakdown`
  - `report_trends`
- FX support for report conversion without synthetic values:
  - wallet-scoped FX list/upsert endpoints
  - explicit `warnings` when FX is missing
- Diagnostics correctness:
  - `diag` version/commit sourced from runtime app state
- New gate script for reports contract verification.

## Invariants Preserved
- Wallet scoping enforced on all report and FX endpoints.
- Sync invariants unchanged:
  - `op_id` idempotency
  - `base_version` conflict behavior
  - soft-delete propagation
  - deterministic pull ordering
- No feature/route removals.

## Gates and Exact Verification Commands
Run with:

```bash
export BASE_URL=https://hisabi.yemenfrappe.com
export ORIGIN=http://localhost:8082
```

Scripts:

```bash
bash hisabi_backend/scripts/verify_auth_smoke.sh
bash hisabi_backend/scripts/verify_sync_pull.sh
bash hisabi_backend/scripts/verify_sync_push_e2e.sh
bash hisabi_backend/scripts/verify_sync_conflict_resolution.sh
bash hisabi_backend/scripts/verify_reports_contract.sh
```

Targeted tests:

```bash
bench --site hisabi.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_transaction_balance_determinism
bench --site hisabi.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_reports_contract_v2
```

Diag expectation:

```bash
curl -sS https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag | jq .
```

Expected keys:
- `message.status = "ok"`
- `message.app.version = "v1.0.0-rc.2"`
- `message.app.commit` is non-empty

## Risk Areas
- Report conversion correctness depends on FX coverage by effective date.
- Conflict-heavy clients can surface more `status=conflict` responses until client rebasing is complete.
- Any client consuming old report keys without envelope handling may require compatibility checks.

## Rollback Notes
- Revert Sprint 03 backend commits in reverse order if rollback is required.
- Keep API contract alignment with frontend to avoid report/filter drift.
- If rolling back reports/FX endpoints, disable corresponding frontend usage in the same release window.
