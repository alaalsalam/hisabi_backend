#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Validate Sprint 11 Review Center issue detection + safe idempotent fixes.
# WHEN: Run after review-center backend changes.
# SAFETY: Uses throwaway ids and wallet-scoped API operations only.
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
ACCOUNT_ID="acc-review-${TS}-${RANDOM}"
CATEGORY_ID="cat-review-${TS}-${RANDOM}"
BUCKET_ID="bucket-review-${TS}-${RANDOM}"
INCOME_TX_ID="tx-review-income-${TS}-${RANDOM}"
EXPENSE_TX_ID="tx-review-expense-${TS}-${RANDOM}"
RULE_ID="rrule-review-${TS}-${RANDOM}"
INSTANCE_ID="rinst-review-${TS}-${RANDOM}"
TODAY=$(date -u +%F)
YESTERDAY=$(date -u -d '-1 day' +%F)
FROM_DATE=$(date -u -d '-3 day' +%F)
TO_DATE=$(date -u -d '+2 day' +%F)

echo "==> Seed account/category/bucket/transactions/rule/orphan-instance via sync_push"
SEED_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {"op_id":"op-acc-review-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","account_name":"Review Cash","account_type":"cash","currency":"SAR","opening_balance":0}},
    {"op_id":"op-cat-review-${TS}","entity_type":"Hisabi Category","entity_id":"${CATEGORY_ID}","operation":"create","payload":{"client_id":"${CATEGORY_ID}","category_name":"Review Category","kind":"expense"}},
    {"op_id":"op-bucket-review-${TS}","entity_type":"Hisabi Bucket","entity_id":"${BUCKET_ID}","operation":"create","payload":{"client_id":"${BUCKET_ID}","title":"Review Bucket"}},
    {"op_id":"op-income-review-${TS}","entity_type":"Hisabi Transaction","entity_id":"${INCOME_TX_ID}","operation":"create","payload":{"client_id":"${INCOME_TX_ID}","transaction_type":"income","amount":100,"currency":"SAR","account":"${ACCOUNT_ID}","date_time":"${TODAY} 10:00:00"}},
    {"op_id":"op-expense-review-${TS}","entity_type":"Hisabi Transaction","entity_id":"${EXPENSE_TX_ID}","operation":"create","payload":{"client_id":"${EXPENSE_TX_ID}","transaction_type":"expense","amount":24,"currency":"SAR","account":"${ACCOUNT_ID}","category":"${CATEGORY_ID}","date_time":"${TODAY} 11:00:00"}},
    {"op_id":"op-rule-review-${TS}","entity_type":"Hisabi Recurring Rule","entity_id":"${RULE_ID}","operation":"create","payload":{"client_id":"${RULE_ID}","title":"Review Rule","transaction_type":"expense","amount":9,"currency":"SAR","category_id":"${CATEGORY_ID}","account_id":"${ACCOUNT_ID}","start_date":"${YESTERDAY}","timezone":"Asia/Aden","rrule_type":"daily","interval":1,"end_mode":"none","is_active":1,"created_from":"cloud"}},
    {"op_id":"op-instance-review-${TS}","entity_type":"Hisabi Recurring Instance","entity_id":"${INSTANCE_ID}","operation":"create","payload":{"client_id":"${INSTANCE_ID}","rule_id":"${RULE_ID}","occurrence_date":"${YESTERDAY}","status":"generated"}}
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

echo "==> review_issues contract + issue discovery"
ISSUES1=$(get_json "/api/method/hisabi_backend.api.v1.review_issues?wallet_id=${WALLET_ID}&from_date=${FROM_DATE}&to_date=${TO_DATE}" "${TOKEN}")
if ! echo "${ISSUES1}" | jq -e '.message.meta.wallet_id and (.message.issues | type == "array") and (.message.stats | type == "object")' >/dev/null; then
  echo "review_issues shape validation failed" >&2
  echo "${ISSUES1}" >&2
  exit 1
fi
TOTAL1=$(echo "${ISSUES1}" | jq -r '.message.stats.total // 0')
if [[ ${TOTAL1} -le 0 ]]; then
  echo "review_issues did not detect any issues" >&2
  echo "${ISSUES1}" >&2
  exit 1
fi

INCOME_ISSUE_ID=$(echo "${ISSUES1}" | jq -r '.message.issues[] | select(.type=="missing_income_allocation") | .issue_id' | head -n1)
EXPENSE_ISSUE_ID=$(echo "${ISSUES1}" | jq -r '.message.issues[] | select(.type=="missing_expense_bucket") | .issue_id' | head -n1)
ORPHAN_ISSUE_ID=$(echo "${ISSUES1}" | jq -r '.message.issues[] | select(.type=="orphan_recurring_instance") | .issue_id' | head -n1)

if [[ -z "${INCOME_ISSUE_ID}" || -z "${EXPENSE_ISSUE_ID}" || -z "${ORPHAN_ISSUE_ID}" ]]; then
  echo "missing expected issue types from review_issues" >&2
  echo "${ISSUES1}" >&2
  exit 1
fi

FIXES_PAYLOAD=$(cat <<JSON
{
  "wallet_id":"${WALLET_ID}",
  "fixes":[
    {"issue_id":"${INCOME_ISSUE_ID}","action":"open_allocation","payload":{"transaction_id":"${INCOME_TX_ID}","bucket_id":"${BUCKET_ID}"}},
    {"issue_id":"${EXPENSE_ISSUE_ID}","action":"assign_bucket","payload":{"transaction_id":"${EXPENSE_TX_ID}","bucket_id":"${BUCKET_ID}"}},
    {"issue_id":"${ORPHAN_ISSUE_ID}","action":"link_or_delete","payload":{"instance_id":"${INSTANCE_ID}","mode":"skip"}}
  ]
}
JSON
)

echo "==> review_apply_fix first pass"
APPLY1=$(post_json "/api/method/hisabi_backend.api.v1.review_apply_fix" "${FIXES_PAYLOAD}" "${TOKEN}")
APPLIED1=$(echo "${APPLY1}" | jq -r '.message.applied // 0')
if [[ ${APPLIED1} -lt 2 ]]; then
  echo "review_apply_fix first pass did not apply expected fixes" >&2
  echo "${APPLY1}" >&2
  exit 1
fi
if ! echo "${APPLY1}" | jq -e '.message.errors | length == 0' >/dev/null; then
  echo "review_apply_fix first pass returned errors" >&2
  echo "${APPLY1}" >&2
  exit 1
fi

ISSUES2=$(get_json "/api/method/hisabi_backend.api.v1.review_issues?wallet_id=${WALLET_ID}&from_date=${FROM_DATE}&to_date=${TO_DATE}" "${TOKEN}")
TOTAL2=$(echo "${ISSUES2}" | jq -r '.message.stats.total // 0')
if [[ ${TOTAL2} -ge ${TOTAL1} ]]; then
  echo "review_issues total did not reduce after fixes" >&2
  echo "before=${TOTAL1} after=${TOTAL2}" >&2
  echo "${ISSUES2}" >&2
  exit 1
fi

echo "==> review_apply_fix second pass (idempotent)"
APPLY2=$(post_json "/api/method/hisabi_backend.api.v1.review_apply_fix" "${FIXES_PAYLOAD}" "${TOKEN}")
APPLIED2=$(echo "${APPLY2}" | jq -r '.message.applied // 0')
if [[ ${APPLIED2} -ne 0 ]]; then
  echo "review_apply_fix second pass is not idempotent" >&2
  echo "${APPLY2}" >&2
  exit 1
fi
if ! echo "${APPLY2}" | jq -e '.message.skipped | length >= 3' >/dev/null; then
  echo "review_apply_fix second pass should report skipped entries" >&2
  echo "${APPLY2}" >&2
  exit 1
fi

echo "REVIEW CENTER VERIFY PASS"
