#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Validate Sprint 06 expense-to-bucket effectiveness report contract.
# WHEN: Run as a release gate after bucket/reporting changes.
# SAFETY: Uses throwaway auth/device/wallet data and app-level API calls only.
# ------------------------------------------------------------------------------
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-bucket-effectiveness-${UNIQUE_SUFFIX}"}

require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for this script" >&2
    exit 1
  fi
}

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

post_json() {
  local endpoint=$1
  local payload=$2
  local token=${3:-}
  if [[ -n "${token}" ]]; then
    curl -s -X POST "${BASE_URL}${endpoint}" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${token}" \
      -d "${payload}"
  else
    curl -s -X POST "${BASE_URL}${endpoint}" \
      -H "Content-Type: application/json" \
      -d "${payload}"
  fi
}

get_json() {
  local endpoint=$1
  local token=$2
  curl -s -X GET "${BASE_URL}${endpoint}" \
    -H "Authorization: Bearer ${token}"
}

require_jq

echo "==> Register throwaway user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Bucket Effectiveness","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Device"}}
JSON
)
REGISTER_RESP=$(post_json "/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
TOKEN=$(echo "${REGISTER_RESP}" | jq -er '.message.auth.token')
WALLET_ID=$(echo "${REGISTER_RESP}" | jq -er '.message.default_wallet_id')

TS=$(date +%s)
ACCOUNT_ID="acc-bucket-effect-${TS}"
BUCKET_ID="bucket-bucket-effect-${TS}"
INCOME_TX_ID="tx-income-bucket-effect-${TS}"
EXPENSE_TX_MAPPED_ID="tx-expense-mapped-bucket-effect-${TS}"
EXPENSE_TX_UNALLOC_ID="tx-expense-unalloc-bucket-effect-${TS}"
ASSIGNMENT_ID="tbexp-bucket-effect-${TS}"

echo "==> Seed account/bucket/transactions via sync_push"
SYNC_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {"op_id":"op-acc-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","account_name":"Cash","account_type":"cash","currency":"SAR","opening_balance":0}},
    {"op_id":"op-bucket-${TS}","entity_type":"Hisabi Bucket","entity_id":"${BUCKET_ID}","operation":"create","payload":{"client_id":"${BUCKET_ID}","title":"Essentials","color":"#10b981","icon":"📦"}},
    {"op_id":"op-income-${TS}","entity_type":"Hisabi Transaction","entity_id":"${INCOME_TX_ID}","operation":"create","payload":{"client_id":"${INCOME_TX_ID}","transaction_type":"income","date_time":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","amount":100,"currency":"SAR","account":"${ACCOUNT_ID}"}},
    {"op_id":"op-exp-map-${TS}","entity_type":"Hisabi Transaction","entity_id":"${EXPENSE_TX_MAPPED_ID}","operation":"create","payload":{"client_id":"${EXPENSE_TX_MAPPED_ID}","transaction_type":"expense","date_time":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","amount":30,"currency":"SAR","account":"${ACCOUNT_ID}"}},
    {"op_id":"op-exp-unalloc-${TS}","entity_type":"Hisabi Transaction","entity_id":"${EXPENSE_TX_UNALLOC_ID}","operation":"create","payload":{"client_id":"${EXPENSE_TX_UNALLOC_ID}","transaction_type":"expense","date_time":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","amount":20,"currency":"SAR","account":"${ACCOUNT_ID}"}}
  ]
}
JSON
)
SYNC_RESP=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_push" "${SYNC_PAYLOAD}" "${TOKEN}")
if ! echo "${SYNC_RESP}" | jq -e '.message.results | all(.status == "accepted" or .status == "duplicate")' >/dev/null; then
  echo "sync_push failed" >&2
  echo "${SYNC_RESP}" >&2
  exit 1
fi

echo "==> Apply 100% income allocation via sync_push"
ALLOC_ROW_ID="tb-bucket-effect-${TS}"
ALLOC_SYNC_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {
      "op_id":"op-alloc-${TS}",
      "entity_type":"Hisabi Transaction Bucket",
      "entity_id":"${ALLOC_ROW_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${ALLOC_ROW_ID}",
        "transaction_id":"${INCOME_TX_ID}",
        "bucket_id":"${BUCKET_ID}",
        "amount":100,
        "percentage":100
      }
    }
  ]
}
JSON
)
ALLOC_SYNC_RESP=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_push" "${ALLOC_SYNC_PAYLOAD}" "${TOKEN}")
if ! echo "${ALLOC_SYNC_RESP}" | jq -e '.message.results | all(.status == "accepted" or .status == "duplicate")' >/dev/null; then
  echo "income allocation sync_push failed" >&2
  echo "${ALLOC_SYNC_RESP}" >&2
  exit 1
fi

echo "==> Assign mapped expense bucket"
ASSIGN_SYNC_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {
      "op_id":"op-assign-${TS}",
      "entity_type":"Hisabi Transaction Bucket Expense",
      "entity_id":"${ASSIGNMENT_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${ASSIGNMENT_ID}",
        "transaction_id":"${EXPENSE_TX_MAPPED_ID}",
        "bucket_id":"${BUCKET_ID}"
      }
    }
  ]
}
JSON
)
ASSIGN_SYNC_RESP=$(post_json "/api/method/hisabi_backend.api.v1.sync.sync_push" "${ASSIGN_SYNC_PAYLOAD}" "${TOKEN}")
if ! echo "${ASSIGN_SYNC_RESP}" | jq -e '.message.results | all(.status == "accepted" or .status == "duplicate")' >/dev/null; then
  echo "expense assignment sync_push failed" >&2
  echo "${ASSIGN_SYNC_RESP}" >&2
  exit 1
fi

echo "==> Verify report_bucket_effectiveness contract + values"
REPORT_RESP=$(get_json "/api/method/hisabi_backend.api.v1.reports_finance.report_bucket_effectiveness?wallet_id=${WALLET_ID}&currency=SAR" "${TOKEN}")
REPORT_MSG=$(echo "${REPORT_RESP}" | jq -c '.message // .')
REPORT_MSG_JSON="${REPORT_MSG}" BUCKET_ID="${BUCKET_ID}" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["REPORT_MSG_JSON"])
bucket_id = os.environ["BUCKET_ID"]

for key in ("data", "unallocated", "currency", "warnings", "server_time"):
    if key not in payload:
        print(f"missing key: {key}", file=sys.stderr)
        sys.exit(1)

rows = payload.get("data") or []
bucket_row = next((row for row in rows if row.get("bucket_id") == bucket_id), None)
if not bucket_row:
    print("bucket row missing", file=sys.stderr)
    sys.exit(1)

income = float(bucket_row.get("income_allocated") or 0)
expense = float(bucket_row.get("expenses_assigned") or 0)
net = float(bucket_row.get("net") or 0)
if abs(income - 100.0) > 0.01:
    print(f"unexpected income_allocated: {income}", file=sys.stderr)
    sys.exit(1)
if abs(expense - 30.0) > 0.01:
    print(f"unexpected expenses_assigned: {expense}", file=sys.stderr)
    sys.exit(1)
if abs(net - 70.0) > 0.01:
    print(f"unexpected net: {net}", file=sys.stderr)
    sys.exit(1)

unallocated = payload.get("unallocated") or {}
if (unallocated.get("bucket_id") or "") != "unallocated":
    print("unallocated key missing", file=sys.stderr)
    sys.exit(1)
if abs(float(unallocated.get("expenses_assigned") or 0) - 20.0) > 0.01:
    print("unexpected unallocated expenses", file=sys.stderr)
    sys.exit(1)

print("bucket_effectiveness OK")
PY

echo "PASS: verify_bucket_effectiveness"
