#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Release/operator verification for sync conflict contract behavior.
# WHEN: Run pre-release and post-deploy against a target Hisabi backend.
# SAFETY: Uses throwaway generated user/device IDs only; no destructive server ops.
# ------------------------------------------------------------------------------
set -euo pipefail

BASE_URL=${BASE_URL:-"https://expense.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-sync-conflict-$(date +%s)"}

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

PHONE=${PHONE:-"$(generate_valid_phone)"}

json_get() {
  python3 -c $'import json,sys\nraw=sys.stdin.read()\npath=sys.argv[1].split(".")\ndata=json.loads(raw)\nfor p in path:\n    if isinstance(data, dict):\n        data = data.get(p)\n    elif isinstance(data, list) and p.isdigit():\n        idx = int(p)\n        data = data[idx] if 0 <= idx < len(data) else None\n    else:\n        data = None\n    if data is None:\n        break\nif data is None:\n    sys.exit(1)\nprint(data)\n' "$1"
}

curl_with_status() {
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

print_status_and_body() {
  local response="$1"
  local status
  status=$(echo "${response}" | sed -n 's/HTTP_STATUS://p' | tail -n 1)
  local body
  body=$(echo "${response}" | sed '/HTTP_STATUS:/d')
  echo "HTTP ${status}"
  echo "${body}"
}

response_body() {
  local response="$1"
  RESPONSE_RAW="${response}" python3 - <<'PY'
import os
raw = os.environ.get("RESPONSE_RAW", "")
raw = raw.split("HTTP_STATUS:")[0] if "HTTP_STATUS:" in raw else raw
start = raw.find("{")
end = raw.rfind("}")
if start != -1 and end != -1 and end >= start:
    print(raw[start:end + 1].strip())
else:
    print("")
PY
}

http_status() {
  echo "$1" | sed -n 's/HTTP_STATUS://p' | tail -n 1
}

require_http_200() {
  local response="$1"
  local context=${2:-"Expected HTTP 200"}
  local status
  status=$(http_status "${response}")
  if [[ "${status}" != "200" ]]; then
    echo "${context}" >&2
    echo "${response}" >&2
    exit 1
  fi
}

pull_since() {
  local since="$1"
  local response
  response=$(curl -s -G -w "\nHTTP_STATUS:%{http_code}\n" \
    "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" \
    -H "Authorization: Bearer ${TOKEN}" \
    --data-urlencode "device_id=${DEVICE_ID}" \
    --data-urlencode "wallet_id=${WALLET_ID}" \
    --data-urlencode "since=${since}" \
    --data-urlencode "limit=200")
  require_http_200 "${response}" "sync_pull failed during conflict verification"
  response_body "${response}"
}

pull_item_value() {
  local body="$1"
  local entity_type="$2"
  local client_id="$3"
  local path="$4"
  ENTITY_TYPE="${entity_type}" CLIENT_ID="${client_id}" FIELD_PATH="${path}" PULL_BODY="${body}" python3 - <<'PY'
import json, os
body = os.environ.get("PULL_BODY", "")
entity_type = os.environ.get("ENTITY_TYPE")
client_id = os.environ.get("CLIENT_ID")
path = os.environ.get("FIELD_PATH", "").split(".")
try:
    data = json.loads(body) if body else {}
except json.JSONDecodeError:
    data = {}
items = (data.get("message") or {}).get("items") or []
item = None
for row in items:
    if row.get("entity_type") != entity_type:
        continue
    if row.get("client_id") == client_id or row.get("entity_id") == client_id:
        item = row
        break
if not item:
    print("")
    raise SystemExit
value = item
for key in path:
    if isinstance(value, dict):
        value = value.get(key)
    else:
        value = None
    if value is None:
        break
print("" if value is None else value)
PY
}

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Conflict User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Conflict Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
print_status_and_body "${REGISTER_RESP}"
require_http_200 "${REGISTER_RESP}" "register_user failed"
REGISTER_BODY=$(response_body "${REGISTER_RESP}")

TOKEN=$(echo "${REGISTER_BODY}" | json_get message.auth.token || true)
WALLET_ID=$(echo "${REGISTER_BODY}" | json_get message.default_wallet_id || true)
if [[ -z "${TOKEN}" || -z "${WALLET_ID}" ]]; then
  echo "Missing token or default_wallet_id in register response" >&2
  echo "${REGISTER_BODY}" >&2
  exit 1
fi

TS=$(date +%s)
ACCOUNT_ID="acc-conflict-${TS}"
SINCE=$(date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%SZ')

echo "==> Sync push (create account)"
ACCOUNT_CREATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-conf-create-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","name":"Conflict Cash","type":"cash","currency":"SAR"}}
]}
JSON
)
ACCOUNT_CREATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_CREATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_CREATE_RESP}"
require_http_200 "${ACCOUNT_CREATE_RESP}" "create account failed"
ACCOUNT_CREATE_BODY=$(response_body "${ACCOUNT_CREATE_RESP}")
ACCOUNT_VERSION=$(echo "${ACCOUNT_CREATE_BODY}" | json_get message.results.0.doc_version || true)
if [[ -z "${ACCOUNT_VERSION}" ]]; then
  echo "Missing doc_version on account create" >&2
  echo "${ACCOUNT_CREATE_BODY}" >&2
  exit 1
fi

echo "==> Sync push (update account, valid base_version)"
ACCOUNT_UPDATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-conf-update-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"update","base_version":${ACCOUNT_VERSION},"payload":{"client_id":"${ACCOUNT_ID}","account_name":"Conflict Cash Updated"}}
]}
JSON
)
ACCOUNT_UPDATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_UPDATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_UPDATE_RESP}"
require_http_200 "${ACCOUNT_UPDATE_RESP}" "update account failed"
ACCOUNT_UPDATE_BODY=$(response_body "${ACCOUNT_UPDATE_RESP}")
ACCOUNT_VERSION=$(echo "${ACCOUNT_UPDATE_BODY}" | json_get message.results.0.doc_version || true)
if [[ -z "${ACCOUNT_VERSION}" ]]; then
  echo "Missing doc_version on account update" >&2
  echo "${ACCOUNT_UPDATE_BODY}" >&2
  exit 1
fi

# Keep base_version aligned with latest authoritative row state.
# Account derived recalculations can bump doc_version after a successful update.
PULL_BODY=$(pull_since "${SINCE}")
PULL_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -n "${PULL_VERSION}" ]]; then
  ACCOUNT_VERSION="${PULL_VERSION}"
fi

CONFLICT_BASE_VERSION=$((ACCOUNT_VERSION - 1))
if [[ "${CONFLICT_BASE_VERSION}" -lt 0 ]]; then
  CONFLICT_BASE_VERSION=0
fi

echo "==> Sync push (intentional conflict)"
ACCOUNT_CONFLICT_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-conflict-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"update","base_version":${CONFLICT_BASE_VERSION},"payload":{"client_id":"${ACCOUNT_ID}","account_name":"Conflict Should Fail"}}
]}
JSON
)
ACCOUNT_CONFLICT_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_CONFLICT_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_CONFLICT_RESP}"
require_http_200 "${ACCOUNT_CONFLICT_RESP}" "conflict update request failed"
ACCOUNT_CONFLICT_BODY=$(response_body "${ACCOUNT_CONFLICT_RESP}")

CONFLICT_STATUS=$(echo "${ACCOUNT_CONFLICT_BODY}" | json_get message.results.0.status || true)
if [[ "${CONFLICT_STATUS}" != "conflict" ]]; then
  echo "Expected conflict status, got '${CONFLICT_STATUS}'" >&2
  echo "${ACCOUNT_CONFLICT_BODY}" >&2
  exit 1
fi

SERVER_DOC_VERSION=$(echo "${ACCOUNT_CONFLICT_BODY}" | json_get message.results.0.server_doc_version || true)
SERVER_RECORD_VERSION=$(echo "${ACCOUNT_CONFLICT_BODY}" | json_get message.results.0.server_record.doc_version || true)
CLIENT_BASE_VERSION=$(echo "${ACCOUNT_CONFLICT_BODY}" | json_get message.results.0.client_base_version || true)
SERVER_DOC_CLIENT_ID=$(echo "${ACCOUNT_CONFLICT_BODY}" | json_get message.results.0.server_doc.client_id || true)
if [[ -z "${SERVER_DOC_VERSION}" || -z "${SERVER_RECORD_VERSION}" || -z "${CLIENT_BASE_VERSION}" || -z "${SERVER_DOC_CLIENT_ID}" ]]; then
  echo "Conflict payload is missing required fields" >&2
  echo "${ACCOUNT_CONFLICT_BODY}" >&2
  exit 1
fi
if [[ "${SERVER_DOC_VERSION}" != "${ACCOUNT_VERSION}" || "${SERVER_RECORD_VERSION}" != "${ACCOUNT_VERSION}" ]]; then
  echo "Conflict server version mismatch (expected ${ACCOUNT_VERSION})" >&2
  echo "${ACCOUNT_CONFLICT_BODY}" >&2
  exit 1
fi
if [[ "${SERVER_DOC_CLIENT_ID}" != "${ACCOUNT_ID}" ]]; then
  echo "Conflict server_doc.client_id mismatch (expected ${ACCOUNT_ID}, got ${SERVER_DOC_CLIENT_ID})" >&2
  echo "${ACCOUNT_CONFLICT_BODY}" >&2
  exit 1
fi

echo "==> Sync pull (ensure conflict did not mutate version)"
PULL_BODY=$(pull_since "${SINCE}")
PULL_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -z "${PULL_VERSION}" || "${PULL_VERSION}" != "${ACCOUNT_VERSION}" ]]; then
  echo "Account doc_version changed after conflict (expected ${ACCOUNT_VERSION}, got ${PULL_VERSION})" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi

echo "Conflict resolution contract OK."
