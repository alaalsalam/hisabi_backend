# CHANGELOG_V1_RC1

Release: `v1.0.0-rc.1`

## Milestones Included
- Auth v2 endpoints stabilized with device/session lockout compatibility.
- Sync invariants enforced (base-version validation, idempotent operations, soft-delete handling, pull ordering safety).
- Reports reliability fixes: wallet-scoped validation and structured `invalid_request` responses.
- Balance/currency regression fixes verified by sync/report suite.
- Release readiness docs shipped (`SMOKE_TEST_V1`, `RELEASE_READY_V1`, RC deploy runbook).
- Safe operations diagnostic endpoint added: `hisabi_backend.api.v1.health.diag`.

## Required Verification Commands
```bash
cd /home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts
BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_auth_smoke.sh
BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_pull.sh
BASE_URL=https://expense.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_push_e2e.sh
curl -sS https://expense.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag | jq .
```
