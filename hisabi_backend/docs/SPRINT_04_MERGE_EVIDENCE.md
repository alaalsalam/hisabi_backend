# Sprint 04 Merge Evidence (Backend)

Date: 2026-02-10T09:50:52+01:00

## Merge Details
- main before: 25bc2976054a40acb593b48fa583dc462edf08dc
- main after: 2730e38e9acca901c07fb6684da7e8e63e9d185c
- merged-from branch: sprint/04-buckets
- merged-from SHA: 2730e38e9acca901c07fb6684da7e8e63e9d185c
- merge method: fast-forward
- conflicts: none

## Post-merge Localhost Gates
- BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_auth_smoke.sh: PASS
- BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_sync_pull.sh: PASS
- BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_sync_push_e2e.sh: PASS
- BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_sync_conflict_resolution.sh: PASS
- BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash hisabi_backend/hisabi_backend/scripts/verify_bucket_reports.sh: PASS
- diag: PASS (`hisabi_backend.api.v1.health.diag`)
