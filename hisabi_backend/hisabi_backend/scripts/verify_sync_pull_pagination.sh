#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://expense.yemenfrappe.com"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-sync-pull-page-${UNIQUE_SUFFIX}"}

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

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required for this script" >&2
    exit 1
  fi
}

curl_json() {
  local method=$1
  local url=$2
  local payload=${3:-}
  local token=${4:-}
  if [[ -n "${payload}" ]]; then
    curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
      -H "Content-Type: application/json" \
      -H "Origin: ${ORIGIN}" \
      ${token:+-H "Authorization: Bearer ${token}"} \
      -d "${payload}"
  else
    curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X "${method}" "${url}" \
      -H "Origin: ${ORIGIN}" \
      ${token:+-H "Authorization: Bearer ${token}"}
  fi
}

response_status() {
  echo "$1" | sed -n 's/HTTP_STATUS://p' | tail -n 1
}

response_body() {
  echo "$1" | sed '/HTTP_STATUS:/d'
}

assert_http_200() {
  local response="$1"
  local label="$2"
  local status
  status=$(response_status "${response}")
  if [[ "${status}" != "200" ]]; then
    echo "${label}: expected HTTP 200, got HTTP ${status}" >&2
    response_body "${response}" >&2
    exit 1
  fi
}

require_bin jq

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Sync Pull Pagination User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Sync Pull Pagination Device"}}
JSON
)
REGISTER_RESP=$(curl_json POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
assert_http_200 "${REGISTER_RESP}" "register"
REGISTER_BODY=$(response_body "${REGISTER_RESP}")
TOKEN=$(echo "${REGISTER_BODY}" | jq -er '.message.auth.token')
WALLET_ID=$(echo "${REGISTER_BODY}" | jq -er '.message.default_wallet_id')

TS=$(date +%s)
PREFIX="acc-page-${TS}"

for i in 1 2 3 4 5; do
  ACCOUNT_ID="${PREFIX}-${i}"
  OP_ID="op-page-${TS}-${i}"
  CREATE_PUSH=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"${OP_ID}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","account_name":"Page ${i}","account_type":"cash","currency":"SAR"}}
]}
JSON
)
  CREATE_RESP=$(curl_json POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CREATE_PUSH}" "${TOKEN}")
  assert_http_200 "${CREATE_RESP}" "sync_push create ${ACCOUNT_ID}"
  CREATE_BODY=$(response_body "${CREATE_RESP}")
  CREATE_STATUS=$(echo "${CREATE_BODY}" | jq -er '.message.results[0].status')
  if [[ "${CREATE_STATUS}" != "accepted" && "${CREATE_STATUS}" != "duplicate" && "${CREATE_STATUS}" != "noop" ]]; then
    echo "Unexpected create status ${CREATE_STATUS} for ${ACCOUNT_ID}" >&2
    echo "${CREATE_BODY}" >&2
    exit 1
  fi
done

declare -A SEEN=()
cursor=""
SINCE=$(date -u -d '2 days ago' '+%Y-%m-%dT%H:%M:%SZ')

for _page in $(seq 1 20); do
  if [[ -n "${cursor}" ]]; then
    PULL_RESP=$(curl -sS -G -w "\nHTTP_STATUS:%{http_code}\n" "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Origin: ${ORIGIN}" \
      --data-urlencode "device_id=${DEVICE_ID}" \
      --data-urlencode "wallet_id=${WALLET_ID}" \
      --data-urlencode "cursor=${cursor}" \
      --data-urlencode "limit=2")
  else
    PULL_RESP=$(curl -sS -G -w "\nHTTP_STATUS:%{http_code}\n" "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Origin: ${ORIGIN}" \
      --data-urlencode "device_id=${DEVICE_ID}" \
      --data-urlencode "wallet_id=${WALLET_ID}" \
      --data-urlencode "since=${SINCE}" \
      --data-urlencode "limit=2")
  fi

  assert_http_200 "${PULL_RESP}" "sync_pull page"
  PULL_BODY=$(response_body "${PULL_RESP}")

  while IFS= read -r id; do
    [[ -z "${id}" ]] && continue
    if [[ -n "${SEEN[${id}]:-}" ]]; then
      echo "Duplicate entity_id detected across pages: ${id}" >&2
      echo "${PULL_BODY}" >&2
      exit 1
    fi
    SEEN["${id}"]=1
  done < <(echo "${PULL_BODY}" | jq -r --arg prefix "${PREFIX}-" '.message.items[]? | select(.entity_type=="Hisabi Account" and (.client_id | startswith($prefix))) | .client_id')

  HAS_MORE=$(echo "${PULL_BODY}" | jq -r '.message.has_more')
  NEXT_CURSOR=$(echo "${PULL_BODY}" | jq -r '.message.next_cursor // ""')
  if [[ "${HAS_MORE}" == "true" ]]; then
    if [[ -z "${NEXT_CURSOR}" || "${NEXT_CURSOR}" == "${cursor}" ]]; then
      echo "Cursor stalled during pagination" >&2
      echo "${PULL_BODY}" >&2
      exit 1
    fi
    cursor="${NEXT_CURSOR}"
    continue
  fi
  break
done

if [[ "${#SEEN[@]}" -ne 5 ]]; then
  echo "Expected 5 unique paginated accounts, found ${#SEEN[@]}" >&2
  printf 'seen: %s\n' "${!SEEN[@]}" >&2
  exit 1
fi

echo "verify_sync_pull_pagination.sh: OK"
