# RELEASE_READY_V1

Target production host: `https://hisabi.yemenfrappe.com`

## Pre-Release
1. Verify site encryption key is configured:
```bash
cd /home/frappe/frappe-bench
bench --site hisabi.yemenfrappe.com console <<'PY'
import frappe
print(bool(frappe.local.conf.get("encryption_key")))
PY
```
Expected: prints `True`.

2. Build frontend (must pass):
```bash
cd /home/frappe/frappe-bench/apps/hisabi_backend/hisabi-your-smart-wallet
npm run build
```

3. Run backend verification scripts against production URL with local origin:
```bash
cd /home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts
BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_auth_smoke.sh
BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_pull.sh
BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_push_e2e.sh
```

## During Release
1. Migrate and clear cache:
```bash
cd /home/frappe/frappe-bench
bench --site hisabi.yemenfrappe.com migrate
bench --site hisabi.yemenfrappe.com clear-cache
```

2. Restart services:
```bash
cd /home/frappe/frappe-bench
bench restart
```

3. Warm key endpoints:
```bash
curl -fsS https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.ping
curl -fsS https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag
```

## Post-Release
1. Monitor logs for 15-30 minutes:
```bash
cd /home/frappe/frappe-bench
bench --site hisabi.yemenfrappe.com logs --web --worker --short
```

2. Verify first real user journey:
- Registration/login succeeds.
- Wallet setup succeeds.
- Offline create -> reconnect -> sync succeeds.

3. Rollback notes:
- Revert app deploy to previous known-good commit.
- Run `bench --site hisabi.yemenfrappe.com migrate` if rollback includes schema change.
- Restart services and re-run `verify_auth_smoke.sh` + `verify_sync_pull.sh`.
