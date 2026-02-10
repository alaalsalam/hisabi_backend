#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Validate Sprint 10 Today Center backend APIs (due + idempotent generate).
# WHEN: Run after recurring/backend changes.
# SAFETY: Uses throwaway ids and additive API operations only.
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASE_URL=${BASE_URL:-"http://127.0.0.1:18000"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}

require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required" >&2
    exit 1
  fi
}

post_json() {
  local endpoint=$1
  local payload=$2
  local token=${3:-}
  if [[ -n "${token}" ]]; then
    curl -sS -X POST "${BASE_URL}${endpoint}" \
      -H 'Content-Type: application/json' \
      -H "Origin: ${ORIGIN}" \
      -H "Authorization: Bearer ${token}" \
      -d "${payload}"
  else
    curl -sS -X POST "${BASE_URL}${endpoint}" \
      -H 'Content-Type: application/json' \
      -H "Origin: ${ORIGIN}" \
      -d "${payload}"
  fi
}

get_json() {
  local endpoint=$1
  local token=$2
  curl -sS -G "${BASE_URL}${endpoint}" \
    -H "Authorization: Bearer ${token}" \
    -H "Origin: ${ORIGIN}"
}

require_jq

eval "$(BASE_URL="${BASE_URL}" ORIGIN="${ORIGIN}" bash "${SCRIPT_DIR}/mint_device_token.sh")"
if [[ -z "${HISABI_TOKEN:-}" ]]; then
  echo "mint_device_token.sh did not return token" >&2
  exit 1
fi
TOKEN=${HISABI_TOKEN}

ME_RESP=$(get_json "/api/method/hisabi_backend.api.v1.me" "${TOKEN}")
WALLET_ID=$(echo "${ME_RESP}" | jq -r '.message.default_wallet_id // empty')
DEVICE_ID=$(echo "${ME_RESP}" | jq -r '.message.device.device_id // empty')
if [[ -z "${WALLET_ID}" || -z "${DEVICE_ID}" ]]; then
  echo "failed to resolve wallet/device from /me" >&2
  echo "${ME_RESP}" >&2
  exit 1
fi

TS=$(date +%s)
ACCOUNT_ID="acc-today-${TS}-${RANDOM}"
CATEGORY_ID="cat-today-${TS}-${RANDOM}"
RULE_ID="rrule-today-${TS}-${RANDOM}"
FROM_DATE=$(date -u +%F)
TO_DATE=$(date -u -d '+7 day' +%F)

echo "==> Seed account + category via sync_push"
SEED_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {"op_id":"op-acc-today-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","account_name":"Today Cash","account_type":"cash","currency":"SAR","opening_balance":0}},
    {"op_id":"op-cat-today-${TS}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"create","payload":{"client_id":"${CATEGORY_ID}","category_name":"Today Category","kind":"expense"}}
  ]
}
JSON
)
SEED_RESP=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_push" "${SEED_PAYLOAD}" "${TOKEN}")
if ! echo "${SEED_RESP}" | jq -e '.message.results | all(.status == "accepted" or .status == "duplicate")' >/dev/null; then
  echo "seed sync_push failed" >&2
  echo "${SEED_RESP}" >&2
  exit 1
fi

echo "==> Create recurring rule"
RULE_PAYLOAD=$(cat <<JSON
{
  "wallet_id":"${WALLET_ID}",
  "client_id":"${RULE_ID}",
  "title":"Today Daily Rule",
  "transaction_type":"expense",
  "amount":9.5,
  "currency":"SAR",
  "category_id":"${CATEGORY_ID}",
  "account_id":"${ACCOUNT_ID}",
  "start_date":"${FROM_DATE}",
  "rrule_type":"daily",
  "interval":1,
  "end_mode":"none",
  "is_active":1,
  "created_from":"cloud"
}
JSON
)
RULE_RESP=$(post_json "/api/method/hisabi_backend.api.v1.recurring.upsert_rule" "${RULE_PAYLOAD}" "${TOKEN}")
if [[ "$(echo "${RULE_RESP}" | jq -r '.message.status // empty')" != "ok" ]]; then
  echo "rule upsert failed" >&2
  echo "${RULE_RESP}" >&2
  exit 1
fi

echo "==> Call recurring_due"
DUE_ENDPOINT="/api/method/hisabi_backend.api.v1.recurring_due?wallet_id=${WALLET_ID}&from_date=${FROM_DATE}&to_date=${TO_DATE}"
DUE_RESP=$(get_json "${DUE_ENDPOINT}" "${TOKEN}")
if ! echo "${DUE_RESP}" | jq -e '.message.meta.wallet_id and (.message.due_instances | type == "array") and (.message.stats | type == "object")' >/dev/null; then
  echo "recurring_due shape validation failed" >&2
  echo "${DUE_RESP}" >&2
  exit 1
fi
if [[ "$(echo "${DUE_RESP}" | jq -r '.message.meta.wallet_id // empty')" != "${WALLET_ID}" ]]; then
  echo "recurring_due wallet mismatch" >&2
  echo "${DUE_RESP}" >&2
  exit 1
fi

echo "==> Call recurring_generate_due (first pass)"
GEN_PAYLOAD=$(cat <<JSON
{"wallet_id":"${WALLET_ID}","from_date":"${FROM_DATE}","to_date":"${TO_DATE}","mode":"create_missing"}
JSON
)
GEN1_RESP=$(post_json "/api/method/hisabi_backend.api.v1.recurring_generate_due" "${GEN_PAYLOAD}" "${TOKEN}")
GEN1_TX=$(echo "${GEN1_RESP}" | jq -r '.message.created.transactions // 0')
if [[ ${GEN1_TX} -le 0 ]]; then
  echo "recurring_generate_due first pass did not create transactions" >&2
  echo "${GEN1_RESP}" >&2
  exit 1
fi

PULL1=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_pull" "{\"device_id\":\"${DEVICE_ID}\",\"wallet_id\":\"${WALLET_ID}\",\"limit\":1000}" "${TOKEN}")
RULE_INSTANCE_TX_COUNT_1=$(echo "${PULL1}" | jq --arg rid "${RULE_ID}" '[.message.items[] | select(.entity_type == "Hisabi Recurring Instance" and ((.payload.rule_id // "") == $rid) and (((.payload.transaction_id // "") | tostring) != ""))] | length')

echo "==> Call recurring_generate_due (second pass/idempotent)"
GEN2_RESP=$(post_json "/api/method/hisabi_backend.api.v1.recurring_generate_due" "${GEN_PAYLOAD}" "${TOKEN}")
GEN2_TX=$(echo "${GEN2_RESP}" | jq -r '.message.created.transactions // 0')
GEN2_INST=$(echo "${GEN2_RESP}" | jq -r '.message.created.instances // 0')
if [[ ${GEN2_TX} -ne 0 || ${GEN2_INST} -ne 0 ]]; then
  echo "recurring_generate_due second pass is not idempotent" >&2
  echo "${GEN2_RESP}" >&2
  exit 1
fi

PULL2=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_pull" "{\"device_id\":\"${DEVICE_ID}\",\"wallet_id\":\"${WALLET_ID}\",\"limit\":1000}" "${TOKEN}")
RULE_INSTANCE_TX_COUNT_2=$(echo "${PULL2}" | jq --arg rid "${RULE_ID}" '[.message.items[] | select(.entity_type == "Hisabi Recurring Instance" and ((.payload.rule_id // "") == $rid) and (((.payload.transaction_id // "") | tostring) != ""))] | length')
if [[ "${RULE_INSTANCE_TX_COUNT_1}" != "${RULE_INSTANCE_TX_COUNT_2}" ]]; then
  echo "recurring_generate_due second pass changed rule instance tx count" >&2
  echo "before=${RULE_INSTANCE_TX_COUNT_1} after=${RULE_INSTANCE_TX_COUNT_2}" >&2
  exit 1
fi

echo "TODAY CENTER VERIFY PASS"
