#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
ORIGIN=${ORIGIN:-"https://hisabi.yemenfrappe.com"}
PHONE=${PHONE:-"+1555$(date +%s | tail -c 6)"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-$(date +%s)"}

function json_get() {
  python3 -c $'import json,sys\nraw=sys.stdin.read()\npath=sys.argv[1].split(".")\ndata=json.loads(raw)\nfor p in path:\n    data = data.get(p) if isinstance(data, dict) else None\nif data is None:\n    sys.exit(1)\nprint(data)\n' "$1"
}

echo "==> Preflight OPTIONS"
curl -s -i -X OPTIONS \
  "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" \
  -H "Origin: ${ORIGIN}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type, Authorization" | head -n 5

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Device"}}
JSON
)
REGISTER_RESP=$(curl -s -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" \
  -H "Content-Type: application/json" \
  -d "${REGISTER_PAYLOAD}")

echo "Register response: ${REGISTER_RESP}"
TOKEN=$(echo "${REGISTER_RESP}" | json_get message.auth.token || true)
WALLET_ID=$(echo "${REGISTER_RESP}" | json_get message.default_wallet_id || true)

if [[ -z "${TOKEN}" || -z "${WALLET_ID}" ]]; then
  echo "Missing token or wallet id in register response" >&2
  exit 1
fi

echo "==> Login"
LOGIN_PAYLOAD=$(cat <<JSON
{"identifier":"${PHONE}","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android"}}
JSON
)
LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.login" \
  -H "Content-Type: application/json" \
  -d "${LOGIN_PAYLOAD}")

echo "Login response: ${LOGIN_RESP}"

echo "==> Me"
curl -s -X GET "${BASE_URL}/api/method/hisabi_backend.api.v1.me" \
  -H "Authorization: Bearer ${TOKEN}" | head -c 200

echo ""

echo "==> Reports summary"
echo "-- GET with query string"
curl -s "${BASE_URL}/api/method/hisabi_backend.api.v1.reports_finance.report_summary?wallet_id=${WALLET_ID}" \
  -H "Authorization: Bearer ${TOKEN}" | head -c 200

echo ""
echo "-- POST with JSON body"
curl -s -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.reports_finance.report_summary" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"wallet_id\":\"${WALLET_ID}\"}" | head -c 200

echo ""

echo "==> Bucket rules"
curl -s "${BASE_URL}/api/method/hisabi_backend.api.v1.reports.bucket_rules?wallet_id=${WALLET_ID}" \
  -H "Authorization: Bearer ${TOKEN}" | head -c 200

echo ""

echo "==> Sync push (account + transaction + attachment)"
SYNC_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-1","entity_type":"Hisabi Account","entity_id":"acc-verify-1","operation":"create","payload":{"client_id":"acc-verify-1","name":"Cash","type":"cash","currency":"SAR","opening_balance":100,"current_balance":100}},
  {"op_id":"op-tx-1","entity_type":"Hisabi Transaction","entity_id":"tx-verify-1","operation":"create","payload":{"client_id":"tx-verify-1","type":"expense","date_time":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","amount":10,"currency":"SAR","account_id":"acc-verify-1","note":"Verify"}},
  {"op_id":"op-att-1","entity_type":"Hisabi Attachment","entity_id":"att-verify-1","operation":"create","payload":{"client_id":"att-verify-1","owner_entity_type":"Hisabi Transaction","owner_client_id":"tx-verify-1","file_id":"file-verify-1","file_url":"https://example.com/file-verify-1","file_name":"receipt.jpg","mime_type":"image/jpeg","file_size":1234}}
]}
JSON
)

curl -s -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d "${SYNC_PAYLOAD}" | head -c 200

echo ""

echo "Done."
