#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://expense.yemenfrappe.com"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-sync-$(date +%s)"}

generate_valid_phone() {
  local seed
  seed=$(printf '%s' "${UNIQUE_SUFFIX}" | tr -cd '0-9')
  if [[ -z "${seed}" ]]; then
    seed="$(date +%s)$$"
  fi
  local suffix
  suffix=$(printf '%08d' "$((10#${seed} % 100000000))")
  printf '+9677%s' "${suffix}"
}
# QA: generated PHONE must satisfy backend validation rules to avoid false failures.
PHONE=${PHONE:-"$(generate_valid_phone)"}

function json_get() {
  python3 -c $'import json,sys\nraw=sys.stdin.read()\npath=sys.argv[1].split(".")\ndata=json.loads(raw)\nfor p in path:\n    data = data.get(p) if isinstance(data, dict) else None\nif data is None:\n    sys.exit(1)\nprint(data)\n' "$1"
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
  if echo "${body}" | rg -q "http_headers"; then
    echo "Unexpected diagnostics found: http_headers" >&2
    exit 1
  fi
}

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Sync User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Sync Device"}}
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
ACC_ID="acc-sync-${TS}"

echo "==> Sync push (valid minimal payload)"
VALID_SYNC_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-${TS}","entity_type":"Hisabi Account","entity_id":"${ACC_ID}","operation":"create","payload":{"client_id":"${ACC_ID}","name":"Cash","type":"cash","currency":"SAR"}}
]}
JSON
)
VALID_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${VALID_SYNC_PAYLOAD}" "${TOKEN}")
print_status_and_body "${VALID_RESP}"

echo "==> Sync push (mixed: valid + invalid supported type)"
MIXED_VALID_ID="acc-sync-${TS}-ok"
MIXED_INVALID_ID="acc-sync-${TS}-bad"
MIXED_SYNC_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-ok-${TS}","entity_type":"Hisabi Account","entity_id":"${MIXED_VALID_ID}","operation":"create","payload":{"client_id":"${MIXED_VALID_ID}","name":"Cash 2","type":"cash","currency":"SAR"}},
  {"op_id":"op-acc-bad-${TS}","entity_type":"Hisabi Account","entity_id":"${MIXED_INVALID_ID}","operation":"create","payload":{"client_id":"${MIXED_INVALID_ID}","type":"cash","currency":"SAR"}}
]}
JSON
)
MIXED_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${MIXED_SYNC_PAYLOAD}" "${TOKEN}")
print_status_and_body "${MIXED_RESP}"

echo "==> Sync push (invalid: missing wallet_id)"
INVALID_WALLET_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","items":[
  {"op_id":"op-miss-${TS}","entity_type":"Hisabi Account","entity_id":"acc-missing-${TS}","operation":"create","payload":{"client_id":"acc-missing-${TS}","name":"Cash","type":"cash","currency":"SAR"}}
]}
JSON
)
INVALID_WALLET_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${INVALID_WALLET_PAYLOAD}" "${TOKEN}")
print_status_and_body "${INVALID_WALLET_RESP}"

echo "==> Sync push (invalid: unknown entity_type)"
INVALID_ENTITY_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-unknown-${TS}","entity_type":"Hisabi Unknown","entity_id":"unknown-${TS}","operation":"create","payload":{"client_id":"unknown-${TS}","name":"Bad","type":"cash","currency":"SAR"}}
]}
JSON
)
INVALID_ENTITY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${INVALID_ENTITY_PAYLOAD}" "${TOKEN}")
print_status_and_body "${INVALID_ENTITY_RESP}"

echo "==> Sync push (form-encoded params)"
FORM_PAYLOAD="device_id=${DEVICE_ID}&wallet_id=${WALLET_ID}&items=%5B%5D"
FORM_RESP=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "${FORM_PAYLOAD}")
print_status_and_body "${FORM_RESP}"

echo "Done."
