# DEPLOY_RC1_EXPENSE

## Deploy (Operator Commands)
```bash
cd /home/frappe/frappe-bench

git -C apps/hisabi_backend status --short
git -C apps/hisabi_backend fetch --tags
git -C apps/hisabi_backend pull --ff-only

bench --site hisabi.yemenfrappe.com migrate
bench restart
bench --site hisabi.yemenfrappe.com clear-cache
bench --site hisabi.yemenfrappe.com clear-website-cache
```

## Rollback (Operator Commands)
```bash
cd /home/frappe/frappe-bench

# Replace PREVIOUS_TAG with the last known-good backend tag.
git -C apps/hisabi_backend fetch --tags
git -C apps/hisabi_backend checkout PREVIOUS_TAG

bench --site hisabi.yemenfrappe.com migrate
bench restart
```

## Post-Deploy Verification (Commands Only)
```bash
cd /home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts

BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_auth_smoke.sh
BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_pull.sh
BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash verify_sync_push_e2e.sh

curl -sS https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag | jq .
```

## API Contract Spot Checks (Commands Only)
```bash
# Expected: report endpoints do not return HTTP 417.
curl -sS -o /tmp/report_status.json -w "%{http_code}\n" "https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.reports_finance.report_summary"

# Expected: missing wallet_id yields HTTP 422 invalid_request when authenticated.
# Set TOKEN from a successful auth smoke login response.
curl -sS -H "Authorization: Bearer $TOKEN" -o /tmp/report_missing_wallet.json -w "%{http_code}\n" "https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.reports_finance.report_summary"
cat /tmp/report_missing_wallet.json | jq .
```
