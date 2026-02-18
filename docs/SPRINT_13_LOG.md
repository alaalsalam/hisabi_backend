# Sprint 13 Log (Backend) - Boot Performance Support

Date: 2026-02-10  
Branch: `main`

## Scope
- Backend code was not changed in Sprint 13.
- Frontend boot reliability work did not require backend API/contract changes.
- Explicit status: **backend not changed in Sprint 13**.

## Gates Executed
```bash
bench --site hisabi.yemenfrappe.com migrate
BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_local_gate_suite.sh
```

## PASS Evidence
- `bench --site hisabi.yemenfrappe.com migrate`: PASS
  - completed DocType updates and `after_migrate` hooks
  - queued search index rebuild
- local gate suite: PASS
  - `verify_auth_smoke.sh` PASS
  - `verify_sync_pull.sh` PASS
  - `verify_sync_push_e2e.sh` PASS
  - `verify_sync_conflict_resolution.sh` PASS
  - `verify_bucket_reports.sh` PASS
  - `verify_recurring.sh` PASS
  - `verify_today_center.sh` PASS
  - `verify_backup_restore.sh` PASS
  - `verify_review_center.sh` PASS
  - health diag returned `status: ok`
  - final line: `LOCAL GATE SUITE PASS`

## Notes
- No API contract changes.
- No sync/offline behavior removed.

---

## 2026-02-11 Frontend Auth Hotfix (Sessionless Token Login)

### Changes
- Forced auth calls to be sessionless for `/api/method/hisabi_backend.api.v1.register_user` and `/api/method/hisabi_backend.api.v1.login`.
- Removed auth header injection for login/register; credentials are JSON-only.
- Preserved device-token flow and offline-first sync behavior.

### Evidence
- `npm run build`: NOT RUN (local verification pending).
- Login/Register now send no cookies/CSRF headers and do not rely on proxy/session coupling.
