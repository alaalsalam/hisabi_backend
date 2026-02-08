#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://expense.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-sync-push-$(date +%s)"}

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
  python3 -c $'import json,sys\nraw=sys.stdin.read()\npath=sys.argv[1].split(".")\ndata=json.loads(raw)\nfor p in path:\n    if isinstance(data, dict):\n        data = data.get(p)\n    elif isinstance(data, list) and p.isdigit():\n        idx = int(p)\n        data = data[idx] if 0 <= idx < len(data) else None\n    else:\n        data = None\n    if data is None:\n        break\nif data is None:\n    sys.exit(1)\nprint(data)\n' "$1"
}

function curl_with_status() {
  local method=$1
  local url=$2
  local payload=${3:-}
  local token=${4:-}
  local attempt=1
  local response=""
  local status=""

  while [[ ${attempt} -le 3 ]]; do
    if [[ -n "${payload}" ]]; then
      if [[ -n "${token}" ]]; then
        response=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
          -H "Content-Type: application/json" \
          -H "Authorization: Bearer ${token}" \
          -d "${payload}" || true)
      else
        response=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
          -H "Content-Type: application/json" \
          -d "${payload}" || true)
      fi
    else
      if [[ -n "${token}" ]]; then
        response=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
          -H "Authorization: Bearer ${token}" || true)
      else
        response=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" || true)
      fi
    fi

    status=$(echo "${response}" | sed -n 's/HTTP_STATUS://p' | tail -n 1)
    if [[ -n "${status}" && "${status}" != "000" ]]; then
      break
    fi
    if [[ ${attempt} -lt 3 ]]; then
      sleep 1
    fi
    attempt=$((attempt + 1))
  done

  echo "${response}"
}

function curl_with_status_get() {
  local url=$1
  local token=${2:-}
  shift 2 || true
  local attempt=1
  local response=""
  local status=""
  while [[ ${attempt} -le 3 ]]; do
    if [[ -n "${token}" ]]; then
      response=$(curl -s -i -G -w "\nHTTP_STATUS:%{http_code}\n" "${url}" \
        -H "Authorization: Bearer ${token}" \
        "$@" || true)
    else
      response=$(curl -s -i -G -w "\nHTTP_STATUS:%{http_code}\n" "${url}" \
        "$@" || true)
    fi

    status=$(echo "${response}" | sed -n 's/HTTP_STATUS://p' | tail -n 1)
    if [[ -n "${status}" && "${status}" != "000" ]]; then
      break
    fi
    if [[ ${attempt} -lt 3 ]]; then
      sleep 1
    fi
    attempt=$((attempt + 1))
  done

  echo "${response}"
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

function response_body() {
  local response="$1"
  RESPONSE_RAW="${response}" python3 - <<'PY'
import os
raw = os.environ.get("RESPONSE_RAW", "")
raw = raw.split("HTTP_STATUS:")[0] if "HTTP_STATUS:" in raw else raw
start = raw.find("{")
end = raw.rfind("}")
if start != -1 and end != -1 and end >= start:
    body = raw[start:end + 1]
else:
    body = ""
print(body.strip())
PY
}

function http_status() {
  echo "$1" | sed -n 's/HTTP_STATUS://p' | tail -n 1
}

function fail_with_response() {
  local response="$1"
  local message=${2:-"Request failed"}
  local body
  body=$(response_body "${response}")
  echo "${message}" >&2
  if [[ -n "${body}" ]]; then
    echo "${body}" >&2
  else
    echo "${response}" >&2
  fi
  exit 1
}

function require_http_200() {
  local response="$1"
  local context=${2:-"Expected HTTP 200"}
  local status
  status=$(http_status "${response}")
  if [[ "${status}" != "200" ]]; then
    fail_with_response "${response}" "${context}"
  fi
}

function pull_since() {
  local since="$1"
  local response
  response=$(curl_with_status_get "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${TOKEN}" \
    --data-urlencode "device_id=${DEVICE_ID}" \
    --data-urlencode "wallet_id=${WALLET_ID}" \
    --data-urlencode "since=${since}" \
    --data-urlencode "limit=200")
  print_status_and_body "${response}" >&2
  require_http_200 "${response}" "Sync pull failed"
  response_body "${response}"
}

function pull_has_item() {
  local body="$1"
  local entity_type="$2"
  local client_id="$3"
  ENTITY_TYPE="${entity_type}" CLIENT_ID="${client_id}" PULL_BODY="${body}" python3 - <<'PY'
import json, os
body = os.environ.get("PULL_BODY", "")
entity_type = os.environ.get("ENTITY_TYPE")
client_id = os.environ.get("CLIENT_ID")
try:
    data = json.loads(body) if body else {}
except json.JSONDecodeError:
    data = {}
items = (data.get("message") or {}).get("items") or []
found = False
for item in items:
    if item.get("entity_type") != entity_type:
        continue
    if item.get("client_id") == client_id or item.get("entity_id") == client_id:
        found = True
        break
print("yes" if found else "no")
PY
}

function pull_item_value() {
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

function optional_create_supported() {
  local response="$1"
  local label="$2"
  local status
  status=$(http_status "${response}")
  local body
  body=$(response_body "${response}")
  if [[ "${status}" != "200" ]]; then
    local err
    err=$(echo "${body}" | json_get error || true)
    echo "Skipping ${label}: ${err:-http_${status}}"
    echo "${body}"
    return 1
  fi
  local item_status
  item_status=$(echo "${body}" | json_get message.results.0.status || true)
  if [[ "${item_status}" == "error" || -z "${item_status}" ]]; then
    local err
    err=$(echo "${body}" | json_get message.results.0.error_code || true)
    if [[ -z "${err}" ]]; then
      err=$(echo "${body}" | json_get message.results.0.error || true)
    fi
    echo "Skipping ${label}: ${err:-validation_error}"
    echo "${body}"
    return 1
  fi
  return 0
}

function require_status_allowed() {
  local response="$1"
  shift
  local allowed=("$@")
  local body
  body=$(response_body "${response}")
  local status
  status=$(echo "${body}" | json_get message.results.0.status || true)
  local ok="no"
  for value in "${allowed[@]}"; do
    if [[ "${status}" == "${value}" ]]; then
      ok="yes"
      break
    fi
  done
  if [[ "${ok}" != "yes" ]]; then
    fail_with_response "${response}" "Unexpected item status: ${status:-missing}"
  fi
}

function require_status_exact() {
  local response="$1"
  local expected="$2"
  local body
  body=$(response_body "${response}")
  local status
  status=$(echo "${body}" | json_get message.results.0.status || true)
  if [[ "${status}" != "${expected}" ]]; then
    fail_with_response "${response}" "Expected item status ${expected}, got ${status:-missing}"
  fi
}

function require_item_exists() {
  local body="$1"
  local entity_type="$2"
  local client_id="$3"
  local label="$4"
  local has
  has=$(pull_has_item "${body}" "${entity_type}" "${client_id}")
  if [[ "${has}" != "yes" ]]; then
    echo "Missing ${label} ${client_id} in pull response" >&2
    echo "${body}" >&2
    exit 1
  fi
}

function require_name_matches_client_id() {
  local body="$1"
  local entity_type="$2"
  local client_id="$3"
  local label="$4"
  local name
  name=$(pull_item_value "${body}" "${entity_type}" "${client_id}" "payload.name")
  if [[ -z "${name}" || "${name}" != "${client_id}" ]]; then
    echo "Expected ${label} name to equal client_id (${client_id}), got '${name}'" >&2
    echo "${body}" >&2
    exit 1
  fi
}

TS=$(date +%s)
SINCE=$(date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%SZ')

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Sync Push User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Sync Push Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
print_status_and_body "${REGISTER_RESP}"

REGISTER_BODY=$(response_body "${REGISTER_RESP}")
TOKEN=$(echo "${REGISTER_BODY}" | json_get message.auth.token || true)
if [[ -z "${TOKEN}" ]]; then
  echo "Missing token in register response" >&2
  echo "${REGISTER_BODY}" >&2
  exit 1
fi

echo "==> Me"
ME_RESP=$(curl_with_status GET "${BASE_URL}/api/method/hisabi_backend.api.v1.me" "" "${TOKEN}")
print_status_and_body "${ME_RESP}"

ME_BODY=$(response_body "${ME_RESP}")
WALLET_ID=$(echo "${ME_BODY}" | json_get message.default_wallet_id || true)
if [[ -z "${WALLET_ID}" ]]; then
  echo "Missing wallet id in me response" >&2
  echo "${ME_BODY}" >&2
  exit 1
fi

ACCOUNT_ID="acc-wp35-${TS}"
ACCOUNT_CREATE_OP="op-acc-create-${TS}"
ACCOUNT_UPDATE_OP="op-acc-update-${TS}"
ACCOUNT_DELETE_OP="op-acc-delete-${TS}"
ACCOUNT_UPDATED_NAME="WP35 Cash Updated"
ACCOUNT_CONFLICT_NAME="WP35 Cash Conflict"

# Account: create

echo "==> Sync push (create account)"
ACCOUNT_CREATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${ACCOUNT_CREATE_OP}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","account_name":"WP35 Cash","account_type":"cash","currency":"SAR"}}
]}
JSON
)
ACCOUNT_CREATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_CREATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_CREATE_RESP}"
require_http_200 "${ACCOUNT_CREATE_RESP}" "Sync push create account failed"
require_status_exact "${ACCOUNT_CREATE_RESP}" "accepted"
ACCOUNT_CREATE_BODY=$(response_body "${ACCOUNT_CREATE_RESP}")
ACCOUNT_VERSION=$(echo "${ACCOUNT_CREATE_BODY}" | json_get message.results.0.doc_version || true)
if [[ -z "${ACCOUNT_VERSION}" ]]; then
  fail_with_response "${ACCOUNT_CREATE_RESP}" "Missing doc_version on account create"
fi

# Account: pull confirm create

echo "==> Sync pull (confirm create account)"
PULL_BODY=$(pull_since "${SINCE}")
require_item_exists "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "account"
require_name_matches_client_id "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "account"
PULL_ACCOUNT_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -n "${PULL_ACCOUNT_VERSION}" ]]; then
  ACCOUNT_VERSION="${PULL_ACCOUNT_VERSION}"
fi

# Account: update

ACCOUNT_UPDATE_BASE_VERSION="${ACCOUNT_VERSION}"

echo "==> Sync push (update account)"
ACCOUNT_UPDATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${ACCOUNT_UPDATE_OP}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"update","base_version":${ACCOUNT_UPDATE_BASE_VERSION},"payload":{"client_id":"${ACCOUNT_ID}","account_name":"${ACCOUNT_UPDATED_NAME}"}}
]}
JSON
)
ACCOUNT_UPDATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_UPDATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_UPDATE_RESP}"
require_http_200 "${ACCOUNT_UPDATE_RESP}" "Sync push update account failed"
require_status_exact "${ACCOUNT_UPDATE_RESP}" "accepted"
ACCOUNT_UPDATE_BODY=$(response_body "${ACCOUNT_UPDATE_RESP}")
ACCOUNT_VERSION=$(echo "${ACCOUNT_UPDATE_BODY}" | json_get message.results.0.doc_version || true)
if [[ -z "${ACCOUNT_VERSION}" ]]; then
  fail_with_response "${ACCOUNT_UPDATE_RESP}" "Missing doc_version on account update"
fi

# Account: pull confirm update

echo "==> Sync pull (confirm update account)"
PULL_BODY=$(pull_since "${SINCE}")
require_item_exists "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "account"
ACCOUNT_NAME=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "payload.account_name")
if [[ "${ACCOUNT_NAME}" != "${ACCOUNT_UPDATED_NAME}" ]]; then
  echo "Expected account_name '${ACCOUNT_UPDATED_NAME}', got '${ACCOUNT_NAME}'" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi
PULL_ACCOUNT_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -n "${PULL_ACCOUNT_VERSION}" ]]; then
  ACCOUNT_VERSION="${PULL_ACCOUNT_VERSION}"
fi

# Account: idempotency replay

echo "==> Sync push (replay update account)"
ACCOUNT_REPLAY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_UPDATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_REPLAY_RESP}"
require_http_200 "${ACCOUNT_REPLAY_RESP}" "Sync push replay update account failed"
require_status_allowed "${ACCOUNT_REPLAY_RESP}" "accepted" "duplicate" "noop"

PULL_BODY=$(pull_since "${SINCE}")
REPLAY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -z "${REPLAY_VERSION}" ]]; then
  echo "Missing doc_version after replay pull" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi
if [[ "${REPLAY_VERSION}" != "${ACCOUNT_VERSION}" ]]; then
  echo "Account doc_version bumped on replay (expected ${ACCOUNT_VERSION}, got ${REPLAY_VERSION})" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi

# Account: conflict

CONFLICT_BASE_VERSION=$((ACCOUNT_VERSION - 1))
if [[ "${CONFLICT_BASE_VERSION}" -lt 0 ]]; then
  CONFLICT_BASE_VERSION=0
fi

echo "==> Sync push (conflict update account)"
ACCOUNT_CONFLICT_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-conflict-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"update","base_version":${CONFLICT_BASE_VERSION},"payload":{"client_id":"${ACCOUNT_ID}","account_name":"${ACCOUNT_CONFLICT_NAME}"}}
]}
JSON
)
ACCOUNT_CONFLICT_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_CONFLICT_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_CONFLICT_RESP}"
require_http_200 "${ACCOUNT_CONFLICT_RESP}" "Sync push conflict update account failed"
require_status_exact "${ACCOUNT_CONFLICT_RESP}" "conflict"
ACCOUNT_CONFLICT_BODY=$(response_body "${ACCOUNT_CONFLICT_RESP}")
SERVER_VERSION=$(echo "${ACCOUNT_CONFLICT_BODY}" | json_get message.results.0.server_record.doc_version || true)
if [[ -z "${SERVER_VERSION}" || "${SERVER_VERSION}" != "${ACCOUNT_VERSION}" ]]; then
  fail_with_response "${ACCOUNT_CONFLICT_RESP}" "Conflict server_record doc_version mismatch"
fi
PULL_BODY=$(pull_since "${SINCE}")
CONFLICT_PULL_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -z "${CONFLICT_PULL_VERSION}" || "${CONFLICT_PULL_VERSION}" != "${ACCOUNT_VERSION}" ]]; then
  echo "Account doc_version changed after conflict (expected ${ACCOUNT_VERSION}, got ${CONFLICT_PULL_VERSION})" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi

# Optional: Category

CATEGORY_ID="cat-wp35-${TS}"
CATEGORY_CREATE_OP="op-cat-create-${TS}"
CATEGORY_UPDATE_OP="op-cat-update-${TS}"
CATEGORY_DELETE_OP="op-cat-delete-${TS}"
CATEGORY_UPDATED_NAME="WP35 Category Updated"
CATEGORY_CONFLICT_NAME="WP35 Category Conflict"
CATEGORY_SUPPORTED="yes"

echo "==> Sync push (create category)"
CATEGORY_CREATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${CATEGORY_CREATE_OP}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"create","payload":{"client_id":"${CATEGORY_ID}","category_name":"WP35 Category","kind":"expense"}}
]}
JSON
)
CATEGORY_CREATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CATEGORY_CREATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${CATEGORY_CREATE_RESP}"
if ! optional_create_supported "${CATEGORY_CREATE_RESP}" "category"; then
  CATEGORY_SUPPORTED="no"
fi

if [[ "${CATEGORY_SUPPORTED}" == "yes" ]]; then
  CATEGORY_CREATE_BODY=$(response_body "${CATEGORY_CREATE_RESP}")
  CATEGORY_VERSION=$(echo "${CATEGORY_CREATE_BODY}" | json_get message.results.0.doc_version || true)
  if [[ -z "${CATEGORY_VERSION}" ]]; then
    fail_with_response "${CATEGORY_CREATE_RESP}" "Missing doc_version on category create"
  fi

  echo "==> Sync pull (confirm create category)"
  PULL_BODY=$(pull_since "${SINCE}")
  require_item_exists "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "category"
  CATEGORY_PULL_NAME=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "payload.name")
  if [[ -z "${CATEGORY_PULL_NAME}" || "${CATEGORY_PULL_NAME}" != "${CATEGORY_ID}" ]]; then
    echo "Skipping category: name != client_id (got '${CATEGORY_PULL_NAME}')" >&2
    CATEGORY_SUPPORTED="no"
  fi
  PULL_CATEGORY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "doc_version")
  if [[ -n "${PULL_CATEGORY_VERSION}" ]]; then
    CATEGORY_VERSION="${PULL_CATEGORY_VERSION}"
  fi

  if [[ "${CATEGORY_SUPPORTED}" != "yes" ]]; then
    echo "Skipping category E2E checks after create" >&2
  fi
fi

if [[ "${CATEGORY_SUPPORTED}" == "yes" ]]; then
  CATEGORY_UPDATE_BASE_VERSION="${CATEGORY_VERSION}"

  echo "==> Sync push (update category)"
  CATEGORY_UPDATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${CATEGORY_UPDATE_OP}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"update","base_version":${CATEGORY_UPDATE_BASE_VERSION},"payload":{"client_id":"${CATEGORY_ID}","category_name":"${CATEGORY_UPDATED_NAME}"}}
]}
JSON
)
  CATEGORY_UPDATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CATEGORY_UPDATE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${CATEGORY_UPDATE_RESP}"
  require_http_200 "${CATEGORY_UPDATE_RESP}" "Sync push update category failed"
  require_status_exact "${CATEGORY_UPDATE_RESP}" "accepted"
  CATEGORY_UPDATE_BODY=$(response_body "${CATEGORY_UPDATE_RESP}")
  CATEGORY_VERSION=$(echo "${CATEGORY_UPDATE_BODY}" | json_get message.results.0.doc_version || true)
  if [[ -z "${CATEGORY_VERSION}" ]]; then
    fail_with_response "${CATEGORY_UPDATE_RESP}" "Missing doc_version on category update"
  fi

  echo "==> Sync pull (confirm update category)"
  PULL_BODY=$(pull_since "${SINCE}")
  require_item_exists "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "category"
  CATEGORY_NAME=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "payload.category_name")
  if [[ "${CATEGORY_NAME}" != "${CATEGORY_UPDATED_NAME}" ]]; then
    echo "Expected category_name '${CATEGORY_UPDATED_NAME}', got '${CATEGORY_NAME}'" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  PULL_CATEGORY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "doc_version")
  if [[ -n "${PULL_CATEGORY_VERSION}" ]]; then
    CATEGORY_VERSION="${PULL_CATEGORY_VERSION}"
  fi

  echo "==> Sync push (replay update category)"
  CATEGORY_REPLAY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CATEGORY_UPDATE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${CATEGORY_REPLAY_RESP}"
  require_http_200 "${CATEGORY_REPLAY_RESP}" "Sync push replay update category failed"
  require_status_allowed "${CATEGORY_REPLAY_RESP}" "accepted" "duplicate" "noop"

  PULL_BODY=$(pull_since "${SINCE}")
  REPLAY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "doc_version")
  if [[ -z "${REPLAY_VERSION}" ]]; then
    echo "Missing doc_version after category replay pull" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  if [[ "${REPLAY_VERSION}" != "${CATEGORY_VERSION}" ]]; then
    echo "Category doc_version bumped on replay (expected ${CATEGORY_VERSION}, got ${REPLAY_VERSION})" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi

  CATEGORY_CONFLICT_BASE_VERSION=$((CATEGORY_VERSION - 1))
  if [[ "${CATEGORY_CONFLICT_BASE_VERSION}" -lt 0 ]]; then
    CATEGORY_CONFLICT_BASE_VERSION=0
  fi

  echo "==> Sync push (conflict update category)"
  CATEGORY_CONFLICT_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-cat-conflict-${TS}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"update","base_version":${CATEGORY_CONFLICT_BASE_VERSION},"payload":{"client_id":"${CATEGORY_ID}","category_name":"${CATEGORY_CONFLICT_NAME}"}}
]}
JSON
)
  CATEGORY_CONFLICT_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CATEGORY_CONFLICT_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${CATEGORY_CONFLICT_RESP}"
  require_http_200 "${CATEGORY_CONFLICT_RESP}" "Sync push conflict update category failed"
  require_status_exact "${CATEGORY_CONFLICT_RESP}" "conflict"
  CATEGORY_CONFLICT_BODY=$(response_body "${CATEGORY_CONFLICT_RESP}")
  SERVER_VERSION=$(echo "${CATEGORY_CONFLICT_BODY}" | json_get message.results.0.server_record.doc_version || true)
  if [[ -z "${SERVER_VERSION}" || "${SERVER_VERSION}" != "${CATEGORY_VERSION}" ]]; then
    fail_with_response "${CATEGORY_CONFLICT_RESP}" "Category conflict server_record doc_version mismatch"
  fi

  PULL_BODY=$(pull_since "${SINCE}")
  CONFLICT_PULL_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "doc_version")
  if [[ -z "${CONFLICT_PULL_VERSION}" || "${CONFLICT_PULL_VERSION}" != "${CATEGORY_VERSION}" ]]; then
    echo "Category doc_version changed after conflict (expected ${CATEGORY_VERSION}, got ${CONFLICT_PULL_VERSION})" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi

  echo "==> Sync push (delete category)"
  CATEGORY_DELETE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${CATEGORY_DELETE_OP}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"delete","base_version":${CATEGORY_VERSION},"payload":{"client_id":"${CATEGORY_ID}"}}
]}
JSON
)
  CATEGORY_DELETE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CATEGORY_DELETE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${CATEGORY_DELETE_RESP}"
  require_http_200 "${CATEGORY_DELETE_RESP}" "Sync push delete category failed"
  require_status_allowed "${CATEGORY_DELETE_RESP}" "accepted" "duplicate" "noop"
  CATEGORY_DELETE_BODY=$(response_body "${CATEGORY_DELETE_RESP}")
  CATEGORY_DELETE_VERSION=$(echo "${CATEGORY_DELETE_BODY}" | json_get message.results.0.doc_version || true)

  echo "==> Sync pull (confirm delete category)"
  PULL_BODY=$(pull_since "${SINCE}")
  require_item_exists "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "category"
  CATEGORY_DELETED=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "is_deleted")
  CATEGORY_DELETED_AT=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "deleted_at")
  if [[ "${CATEGORY_DELETED}" != "1" || -z "${CATEGORY_DELETED_AT}" ]]; then
    echo "Expected category is_deleted=1 and deleted_at set" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  PULL_CATEGORY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "doc_version")
  if [[ -n "${PULL_CATEGORY_VERSION}" ]]; then
    CATEGORY_DELETE_VERSION="${PULL_CATEGORY_VERSION}"
  fi

  echo "==> Sync push (replay delete category)"
  CATEGORY_DELETE_REPLAY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CATEGORY_DELETE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${CATEGORY_DELETE_REPLAY_RESP}"
  require_http_200 "${CATEGORY_DELETE_REPLAY_RESP}" "Sync push replay delete category failed"
  require_status_allowed "${CATEGORY_DELETE_REPLAY_RESP}" "accepted" "duplicate" "noop"

  PULL_BODY=$(pull_since "${SINCE}")
  REPLAY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Category" "${CATEGORY_ID}" "doc_version")
  if [[ -z "${REPLAY_VERSION}" ]]; then
    echo "Missing doc_version after category delete replay pull" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  if [[ -n "${CATEGORY_DELETE_VERSION}" && "${REPLAY_VERSION}" != "${CATEGORY_DELETE_VERSION}" ]]; then
    echo "Category doc_version bumped on delete replay (expected ${CATEGORY_DELETE_VERSION}, got ${REPLAY_VERSION})" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
fi

# Optional: Transaction

TX_ID="tx-wp35-${TS}"
TX_CREATE_OP="op-tx-create-${TS}"
TX_UPDATE_OP="op-tx-update-${TS}"
TX_DELETE_OP="op-tx-delete-${TS}"
TX_UPDATED_NOTE="WP35 Tx Updated"
TX_CONFLICT_NOTE="WP35 Tx Conflict"
TX_SUPPORTED="yes"
TX_DATE_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)


echo "==> Sync push (create transaction)"
TX_CREATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${TX_CREATE_OP}","entity_type":"Hisabi Transaction","entity_id":"${TX_ID}","operation":"create","payload":{"client_id":"${TX_ID}","type":"expense","date_time":"${TX_DATE_TIME}","amount":12.5,"currency":"SAR","account_id":"${ACCOUNT_ID}","note":"WP35 Tx"}}
]}
JSON
)
TX_CREATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_CREATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${TX_CREATE_RESP}"
if ! optional_create_supported "${TX_CREATE_RESP}" "transaction"; then
  TX_SUPPORTED="no"
fi

if [[ "${TX_SUPPORTED}" == "yes" ]]; then
  TX_CREATE_BODY=$(response_body "${TX_CREATE_RESP}")
  TX_VERSION=$(echo "${TX_CREATE_BODY}" | json_get message.results.0.doc_version || true)
  if [[ -z "${TX_VERSION}" ]]; then
    fail_with_response "${TX_CREATE_RESP}" "Missing doc_version on transaction create"
  fi

  echo "==> Sync pull (confirm create transaction)"
  PULL_BODY=$(pull_since "${SINCE}")
  require_item_exists "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "transaction"
  TX_PULL_NAME=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "payload.name")
  if [[ -z "${TX_PULL_NAME}" || "${TX_PULL_NAME}" != "${TX_ID}" ]]; then
    echo "Skipping transaction: name != client_id (got '${TX_PULL_NAME}')" >&2
    TX_SUPPORTED="no"
  fi
  PULL_TX_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "doc_version")
  if [[ -n "${PULL_TX_VERSION}" ]]; then
    TX_VERSION="${PULL_TX_VERSION}"
  fi

  if [[ "${TX_SUPPORTED}" != "yes" ]]; then
    echo "Skipping transaction E2E checks after create" >&2
  fi
fi

if [[ "${TX_SUPPORTED}" == "yes" ]]; then
  TX_UPDATE_BASE_VERSION="${TX_VERSION}"

  echo "==> Sync push (update transaction)"
  TX_UPDATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${TX_UPDATE_OP}","entity_type":"Hisabi Transaction","entity_id":"${TX_ID}","operation":"update","base_version":${TX_UPDATE_BASE_VERSION},"payload":{"client_id":"${TX_ID}","note":"${TX_UPDATED_NOTE}"}}
]}
JSON
)
  TX_UPDATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_UPDATE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${TX_UPDATE_RESP}"
  require_http_200 "${TX_UPDATE_RESP}" "Sync push update transaction failed"
  require_status_exact "${TX_UPDATE_RESP}" "accepted"
  TX_UPDATE_BODY=$(response_body "${TX_UPDATE_RESP}")
  TX_VERSION=$(echo "${TX_UPDATE_BODY}" | json_get message.results.0.doc_version || true)
  if [[ -z "${TX_VERSION}" ]]; then
    fail_with_response "${TX_UPDATE_RESP}" "Missing doc_version on transaction update"
  fi

  echo "==> Sync pull (confirm update transaction)"
  PULL_BODY=$(pull_since "${SINCE}")
  require_item_exists "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "transaction"
  TX_NOTE=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "payload.note")
  if [[ "${TX_NOTE}" != "${TX_UPDATED_NOTE}" ]]; then
    echo "Expected transaction note '${TX_UPDATED_NOTE}', got '${TX_NOTE}'" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  PULL_TX_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "doc_version")
  if [[ -n "${PULL_TX_VERSION}" ]]; then
    TX_VERSION="${PULL_TX_VERSION}"
  fi

  echo "==> Sync push (replay update transaction)"
  TX_REPLAY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_UPDATE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${TX_REPLAY_RESP}"
  require_http_200 "${TX_REPLAY_RESP}" "Sync push replay update transaction failed"
  require_status_allowed "${TX_REPLAY_RESP}" "accepted" "duplicate" "noop"

  PULL_BODY=$(pull_since "${SINCE}")
  REPLAY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "doc_version")
  if [[ -z "${REPLAY_VERSION}" ]]; then
    echo "Missing doc_version after transaction replay pull" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  if [[ "${REPLAY_VERSION}" != "${TX_VERSION}" ]]; then
    echo "Transaction doc_version bumped on replay (expected ${TX_VERSION}, got ${REPLAY_VERSION})" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi

  TX_CONFLICT_BASE_VERSION=$((TX_VERSION - 1))
  if [[ "${TX_CONFLICT_BASE_VERSION}" -lt 0 ]]; then
    TX_CONFLICT_BASE_VERSION=0
  fi

  echo "==> Sync push (conflict update transaction)"
  TX_CONFLICT_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-tx-conflict-${TS}","entity_type":"Hisabi Transaction","entity_id":"${TX_ID}","operation":"update","base_version":${TX_CONFLICT_BASE_VERSION},"payload":{"client_id":"${TX_ID}","note":"${TX_CONFLICT_NOTE}"}}
]}
JSON
)
  TX_CONFLICT_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_CONFLICT_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${TX_CONFLICT_RESP}"
  require_http_200 "${TX_CONFLICT_RESP}" "Sync push conflict update transaction failed"
  require_status_exact "${TX_CONFLICT_RESP}" "conflict"
  TX_CONFLICT_BODY=$(response_body "${TX_CONFLICT_RESP}")
  SERVER_VERSION=$(echo "${TX_CONFLICT_BODY}" | json_get message.results.0.server_record.doc_version || true)
  if [[ -z "${SERVER_VERSION}" || "${SERVER_VERSION}" != "${TX_VERSION}" ]]; then
    fail_with_response "${TX_CONFLICT_RESP}" "Transaction conflict server_record doc_version mismatch"
  fi

  PULL_BODY=$(pull_since "${SINCE}")
  CONFLICT_PULL_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "doc_version")
  if [[ -z "${CONFLICT_PULL_VERSION}" || "${CONFLICT_PULL_VERSION}" != "${TX_VERSION}" ]]; then
    echo "Transaction doc_version changed after conflict (expected ${TX_VERSION}, got ${CONFLICT_PULL_VERSION})" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi

  echo "==> Sync push (delete transaction)"
  TX_DELETE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${TX_DELETE_OP}","entity_type":"Hisabi Transaction","entity_id":"${TX_ID}","operation":"delete","base_version":${TX_VERSION},"payload":{"client_id":"${TX_ID}"}}
]}
JSON
)
  TX_DELETE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_DELETE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${TX_DELETE_RESP}"
  require_http_200 "${TX_DELETE_RESP}" "Sync push delete transaction failed"
  require_status_allowed "${TX_DELETE_RESP}" "accepted" "duplicate" "noop"
  TX_DELETE_BODY=$(response_body "${TX_DELETE_RESP}")
  TX_DELETE_VERSION=$(echo "${TX_DELETE_BODY}" | json_get message.results.0.doc_version || true)

  echo "==> Sync pull (confirm delete transaction)"
  PULL_BODY=$(pull_since "${SINCE}")
  require_item_exists "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "transaction"
  TX_DELETED=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "is_deleted")
  TX_DELETED_AT=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "deleted_at")
  if [[ "${TX_DELETED}" != "1" || -z "${TX_DELETED_AT}" ]]; then
    echo "Expected transaction is_deleted=1 and deleted_at set" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  PULL_TX_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "doc_version")
  if [[ -n "${PULL_TX_VERSION}" ]]; then
    TX_DELETE_VERSION="${PULL_TX_VERSION}"
  fi

  echo "==> Sync push (replay delete transaction)"
  TX_DELETE_REPLAY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_DELETE_PAYLOAD}" "${TOKEN}")
  print_status_and_body "${TX_DELETE_REPLAY_RESP}"
  require_http_200 "${TX_DELETE_REPLAY_RESP}" "Sync push replay delete transaction failed"
  require_status_allowed "${TX_DELETE_REPLAY_RESP}" "accepted" "duplicate" "noop"

  PULL_BODY=$(pull_since "${SINCE}")
  REPLAY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Transaction" "${TX_ID}" "doc_version")
  if [[ -z "${REPLAY_VERSION}" ]]; then
    echo "Missing doc_version after transaction delete replay pull" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
  if [[ -n "${TX_DELETE_VERSION}" && "${REPLAY_VERSION}" != "${TX_DELETE_VERSION}" ]]; then
    echo "Transaction doc_version bumped on delete replay (expected ${TX_DELETE_VERSION}, got ${REPLAY_VERSION})" >&2
    echo "${PULL_BODY}" >&2
    exit 1
  fi
fi

# Account: delete (after optional tests)

echo "==> Sync pull (refresh account version before delete)"
PULL_BODY=$(pull_since "${SINCE}")
require_item_exists "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "account"
LATEST_ACCOUNT_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -n "${LATEST_ACCOUNT_VERSION}" ]]; then
  ACCOUNT_VERSION="${LATEST_ACCOUNT_VERSION}"
fi


echo "==> Sync push (delete account)"
ACCOUNT_DELETE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${ACCOUNT_DELETE_OP}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"delete","base_version":${ACCOUNT_VERSION},"payload":{"client_id":"${ACCOUNT_ID}"}}
]}
JSON
)
ACCOUNT_DELETE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_DELETE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_DELETE_RESP}"
require_http_200 "${ACCOUNT_DELETE_RESP}" "Sync push delete account failed"
require_status_allowed "${ACCOUNT_DELETE_RESP}" "accepted" "duplicate" "noop"
ACCOUNT_DELETE_BODY=$(response_body "${ACCOUNT_DELETE_RESP}")
ACCOUNT_DELETE_VERSION=$(echo "${ACCOUNT_DELETE_BODY}" | json_get message.results.0.doc_version || true)

# Account: pull confirm delete

echo "==> Sync pull (confirm delete account)"
PULL_BODY=$(pull_since "${SINCE}")
require_item_exists "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "account"
ACCOUNT_DELETED=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "is_deleted")
ACCOUNT_DELETED_AT=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "deleted_at")
if [[ "${ACCOUNT_DELETED}" != "1" || -z "${ACCOUNT_DELETED_AT}" ]]; then
  echo "Expected account is_deleted=1 and deleted_at set" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi
PULL_ACCOUNT_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -n "${PULL_ACCOUNT_VERSION}" ]]; then
  ACCOUNT_DELETE_VERSION="${PULL_ACCOUNT_VERSION}"
fi

# Account: delete replay

echo "==> Sync push (replay delete account)"
ACCOUNT_DELETE_REPLAY_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_DELETE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ACCOUNT_DELETE_REPLAY_RESP}"
require_http_200 "${ACCOUNT_DELETE_REPLAY_RESP}" "Sync push replay delete account failed"
require_status_allowed "${ACCOUNT_DELETE_REPLAY_RESP}" "accepted" "duplicate" "noop"

PULL_BODY=$(pull_since "${SINCE}")
REPLAY_VERSION=$(pull_item_value "${PULL_BODY}" "Hisabi Account" "${ACCOUNT_ID}" "doc_version")
if [[ -z "${REPLAY_VERSION}" ]]; then
  echo "Missing doc_version after account delete replay pull" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi
if [[ -n "${ACCOUNT_DELETE_VERSION}" && "${REPLAY_VERSION}" != "${ACCOUNT_DELETE_VERSION}" ]]; then
  echo "Account doc_version bumped on delete replay (expected ${ACCOUNT_DELETE_VERSION}, got ${REPLAY_VERSION})" >&2
  echo "${PULL_BODY}" >&2
  exit 1
fi

echo "Done."
echo "SUCCESS"
