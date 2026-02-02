#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
PHONE=${PHONE:-"+1555$(date +%s | tail -c 6)"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-sync-pull-$(date +%s)"}

function json_get() {
  python3 -c $'import json,sys\nraw=sys.stdin.read()\npath=sys.argv[1].split(".")\ndata=json.loads(raw)\nfor p in path:\n    if isinstance(data, dict):\n        data = data.get(p)\n    elif isinstance(data, list) and p.isdigit():\n        idx = int(p)\n        data = data[idx] if 0 <= idx < len(data) else None\n    else:\n        data = None\n    if data is None:\n        break\nif data is None:\n    sys.exit(1)\nprint(data)\n' "$1"
}

function curl_with_status() {
  local method=$1
  local url=$2
  local payload=${3:-}
  local token=${4:-}

  if [[ -n "${payload}" ]]; then
    if [[ -n "${token}" ]]; then
      curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${token}" \
        -d "${payload}"
    else
      curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
        -H "Content-Type: application/json" \
        -d "${payload}"
    fi
  else
    if [[ -n "${token}" ]]; then
      curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
        -H "Authorization: Bearer ${token}"
    else
      curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}"
    fi
  fi
}

function print_status_and_body() {
  local response="$1"
  local status
  status=$(echo "${response}" | sed -n 's/HTTP_STATUS://p' | tail -n 1)
  local body
  body=$(echo "${response}" | sed '/HTTP_STATUS:/d')
  echo "HTTP ${status}"
  echo "${body}"
}

function assert_status() {
  local response="$1"
  local expected="$2"
  local status
  status=$(echo "${response}" | sed -n 's/HTTP_STATUS://p' | tail -n 1)
  if [[ "${status}" != "${expected}" ]]; then
    echo "Expected HTTP ${expected}, got HTTP ${status}" >&2
    exit 1
  fi
}

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Sync Pull User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Sync Pull Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
print_status_and_body "${REGISTER_RESP}"

TOKEN=$(echo "${REGISTER_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.auth.token || true)
if [[ -z "${TOKEN}" ]]; then
  echo "Missing token in register response" >&2
  exit 1
fi

echo "==> Me"
ME_RESP=$(curl_with_status GET "${BASE_URL}/api/method/hisabi_backend.api.v1.me" "" "${TOKEN}")
print_status_and_body "${ME_RESP}"

WALLET_ID=$(echo "${ME_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.default_wallet_id || true)
if [[ -z "${WALLET_ID}" ]]; then
  echo "Missing wallet id in me response" >&2
  exit 1
fi

TS=$(date +%s)
ACC_ID="acc-pull-${TS}"

echo "==> Sync push (create account)"
CREATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-${TS}","entity_type":"Hisabi Account","entity_id":"${ACC_ID}","operation":"create","payload":{"client_id":"${ACC_ID}","name":"Pull Cash","type":"cash","currency":"SAR"}}
]}
JSON
)
CREATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CREATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${CREATE_RESP}"
assert_status "${CREATE_RESP}" "200"

DOC_VERSION=$(echo "${CREATE_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.results.0.doc_version || true)
if [[ -z "${DOC_VERSION}" ]]; then
  echo "Missing doc_version in create response" >&2
  exit 1
fi

SINCE=$(date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%S')

echo "==> Sync pull (since older than creation)"
PULL_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","since":"${SINCE}","limit":50}
JSON
)
PULL_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${PULL_PAYLOAD}" "${TOKEN}")
print_status_and_body "${PULL_RESP}"
assert_status "${PULL_RESP}" "200"

HAS_ITEM=$(echo "${PULL_RESP}" | sed '/HTTP_STATUS:/d' | ACC_ID="${ACC_ID}" python3 - <<PY
import json,sys,os
data=json.load(sys.stdin)
items=(data.get("message") or {}).get("items") or []
acc=os.environ.get("ACC_ID")
found=any((i.get("entity_type") == "Hisabi Account" and (i.get("client_id") == acc or i.get("entity_id") == acc)) for i in items)
print("yes" if found else "no")
PY
)
if [[ "${HAS_ITEM}" != "yes" ]]; then
  echo "Expected account ${ACC_ID} in pull response" >&2
  exit 1
fi

NEXT_CURSOR=$(echo "${PULL_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.next_cursor || true)
if [[ -z "${NEXT_CURSOR}" ]]; then
  echo "Missing next_cursor in pull response" >&2
  exit 1
fi

echo "==> Sync push (delete account)"
DELETE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-del-${TS}","entity_type":"Hisabi Account","entity_id":"${ACC_ID}","operation":"delete","base_version":${DOC_VERSION},"payload":{"client_id":"${ACC_ID}"}}
]}
JSON
)
DELETE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${DELETE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${DELETE_RESP}"
assert_status "${DELETE_RESP}" "200"

echo "==> Sync pull (after delete)"
PULL_AFTER_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","cursor":"${NEXT_CURSOR}","limit":50}
JSON
)
PULL_AFTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${PULL_AFTER_PAYLOAD}" "${TOKEN}")
print_status_and_body "${PULL_AFTER_RESP}"
assert_status "${PULL_AFTER_RESP}" "200"

HAS_DELETE=$(echo "${PULL_AFTER_RESP}" | sed '/HTTP_STATUS:/d' | ACC_ID="${ACC_ID}" python3 - <<PY
import json,sys,os
data=json.load(sys.stdin)
items=(data.get("message") or {}).get("items") or []
acc=os.environ.get("ACC_ID")
for item in items:
    if item.get("entity_type") != "Hisabi Account":
        continue
    if item.get("client_id") != acc and item.get("entity_id") != acc:
        continue
    if item.get("is_deleted") or item.get("deleted_at"):
        print("yes")
        raise SystemExit
print("no")
PY
)
if [[ "${HAS_DELETE}" != "yes" ]]; then
  echo "Expected deleted flags for ${ACC_ID} in pull response" >&2
  exit 1
fi

echo "Done."
