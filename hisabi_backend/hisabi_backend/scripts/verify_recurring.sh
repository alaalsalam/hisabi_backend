#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Validate Recurring Transactions v1 deterministic generation + idempotency.
# WHEN: Run as a gate after recurring/sync changes.
# SAFETY: Uses throwaway auth/wallet data via API methods only; no destructive ops.
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASE_URL=${BASE_URL:-"http://127.0.0.1:18000"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}

require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required" >&2
    exit 1
  fi
}

json_get() {
  local expr=$1
  jq -er "${expr}" 2>/dev/null || true
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
ACCOUNT_ID="acc-rec-${TS}-${RANDOM}"
CATEGORY_ID="cat-rec-${TS}-${RANDOM}"
RULE_ID="rrule-rec-${TS}-${RANDOM}"

FROM_DATE=$(date -u +%F)
TO_DATE=$(date -u -d '+14 day' +%F)
START_DATE=$(date -u +%F)

echo "==> Seed account + category via sync_push"
SEED_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {"op_id":"op-acc-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","account_name":"Recurring Cash","account_type":"cash","currency":"SAR","opening_balance":0}},
    {"op_id":"op-cat-${TS}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"create","payload":{"client_id":"${CATEGORY_ID}","category_name":"Recurring","kind":"expense"}}
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
  "title":"Gate Recurring",
  "transaction_type":"expense",
  "amount":12.5,
  "currency":"SAR",
  "category_id":"${CATEGORY_ID}",
  "account_id":"${ACCOUNT_ID}",
  "start_date":"${START_DATE}",
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

echo "==> Dry run generate"
DRY_PAYLOAD=$(cat <<JSON
{"wallet_id":"${WALLET_ID}","from_date":"${FROM_DATE}","to_date":"${TO_DATE}","dry_run":1}
JSON
)
DRY_RESP=$(post_json "/api/method/hisabi_backend.api.v1.recurring.generate" "${DRY_PAYLOAD}" "${TOKEN}")
if [[ "$(echo "${DRY_RESP}" | jq -r '.message.status // empty')" != "ok" ]]; then
  echo "dry_run generate failed" >&2
  echo "${DRY_RESP}" >&2
  exit 1
fi

echo "==> Write generate"
WRITE_PAYLOAD=$(cat <<JSON
{"wallet_id":"${WALLET_ID}","from_date":"${FROM_DATE}","to_date":"${TO_DATE}","dry_run":0}
JSON
)
WRITE_RESP=$(post_json "/api/method/hisabi_backend.api.v1.recurring.generate" "${WRITE_PAYLOAD}" "${TOKEN}")
GEN_COUNT=$(echo "${WRITE_RESP}" | jq -r '.message.generated // 0')
if [[ "$(echo "${WRITE_RESP}" | jq -r '.message.status // empty')" != "ok" || ${GEN_COUNT} -le 0 ]]; then
  echo "write generate failed" >&2
  echo "${WRITE_RESP}" >&2
  exit 1
fi

echo "==> Idempotent rerun"
RERUN_RESP=$(post_json "/api/method/hisabi_backend.api.v1.recurring.generate" "${WRITE_PAYLOAD}" "${TOKEN}")
RERUN_GEN=$(echo "${RERUN_RESP}" | jq -r '.message.generated // 0')
if [[ "$(echo "${RERUN_RESP}" | jq -r '.message.status // empty')" != "ok" || ${RERUN_GEN} -ne 0 ]]; then
  echo "idempotent rerun failed" >&2
  echo "${RERUN_RESP}" >&2
  exit 1
fi

echo "==> Verify rule instances and linked transactions"
RULES_RESP=$(get_json "/api/method/hisabi_backend.api.v1.recurring.rules_list?wallet_id=${WALLET_ID}" "${TOKEN}")
if ! echo "${RULES_RESP}" | jq -e --arg rule_id "${RULE_ID}" '.message.rules | any(.client_id == $rule_id)' >/dev/null; then
  echo "rule not found in list" >&2
  echo "${RULES_RESP}" >&2
  exit 1
fi

PULL_RESP=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_pull" "{\"device_id\":\"${DEVICE_ID}\",\"wallet_id\":\"${WALLET_ID}\",\"limit\":500}" "${TOKEN}")
if ! echo "${PULL_RESP}" | jq -e '.message.items | any(.entity_type == "Hisabi Recurring Instance")' >/dev/null; then
  echo "no recurring instances found in sync pull" >&2
  echo "${PULL_RESP}" >&2
  exit 1
fi
if ! echo "${PULL_RESP}" | jq -e '.message.items | any(.entity_type == "Hisabi Transaction" and ((.client_id // "") | startswith("rtx-")))' >/dev/null; then
  echo "no generated transaction found in sync pull" >&2
  echo "${PULL_RESP}" >&2
  exit 1
fi

echo "PASS: verify_recurring"
