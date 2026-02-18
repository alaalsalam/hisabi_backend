# Sprint 12 Log (Backend) - Performance Sprint Evidence

Date: 2026-02-10  
Branch: `main`

## Scope
- Backend not changed in Sprint 12.
- No API contract changes.
- No sync/offline behavior changes.

## Required Gates Executed
```bash
bench --site hisabi.yemenfrappe.com migrate
BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_local_gate_suite.sh
```

## PASS Evidence
- `bench --site hisabi.yemenfrappe.com migrate`: PASS
  - `Migrating hisabi.yemenfrappe.com`
  - `Queued rebuilding of search index for hisabi.yemenfrappe.com`
- `verify_local_gate_suite.sh`: PASS
  - `==> PASS verify_auth_smoke.sh`
  - `==> PASS verify_sync_pull.sh`
  - `==> PASS verify_sync_push_e2e.sh`
  - `==> PASS verify_sync_conflict_resolution.sh`
  - `==> PASS verify_bucket_reports.sh`
  - `==> PASS verify_recurring.sh`
  - `==> PASS verify_today_center.sh`
  - `==> PASS verify_backup_restore.sh`
  - `==> PASS verify_review_center.sh`
  - `LOCAL GATE SUITE PASS`

## Notes
- Local gate suite was executed against local server endpoint `127.0.0.1:18000` in single-site serve mode for `hisabi.yemenfrappe.com`.
