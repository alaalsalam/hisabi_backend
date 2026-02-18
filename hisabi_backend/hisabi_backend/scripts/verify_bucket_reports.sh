#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Verify Sprint 04 bucket-report contracts against real API behavior.
# WHEN: Run as a release gate for bucket reporting changes.
# SAFETY: Uses throwaway user/wallet identifiers and non-destructive API calls.
# ------------------------------------------------------------------------------
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-bucket-reports-${UNIQUE_SUFFIX}"}

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

curl_get_with_status() {
  local url=$1
  local token=${2:-}
  shift 2 || true
  if [[ -n "${token}" ]]; then
    curl -s -G -w "\nHTTP_STATUS:%{http_code}\n" "${url}" \
      -H "Authorization: Bearer ${token}" \
      "$@"
  else
    curl -s -G -w "\nHTTP_STATUS:%{http_code}\n" "${url}" \
      "$@"
  fi
}

http_status() {
  echo "$1" | sed -n 's/HTTP_STATUS://p' | tail -n 1
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

print_status_and_body() {
  local response="$1"
  local status
  status=$(http_status "${response}")
  local body
  body=$(echo "${response}" | sed '/HTTP_STATUS:/d')
  echo "HTTP ${status}"
  echo "${body}"
}

require_http_status() {
  local response="$1"
  local expected="$2"
  local status
  status=$(http_status "${response}")
  if [[ "${status}" != "${expected}" ]]; then
    echo "Expected HTTP ${expected}, got ${status}" >&2
    print_status_and_body "${response}" >&2
    exit 1
  fi
}

assert_report_shape() {
  local body="$1"
  REPORT_BODY="${body}" python3 - <<'PY'
import json, os, sys
body = os.environ.get("REPORT_BODY", "")
try:
    data = json.loads(body) if body else {}
except json.JSONDecodeError:
    print("invalid json", file=sys.stderr)
    sys.exit(1)
msg = data.get("message") if isinstance(data.get("message"), dict) else data
for key in ("data", "warnings", "server_time"):
    if key not in msg:
        print(f"missing key: {key}", file=sys.stderr)
        sys.exit(1)
if not isinstance(msg.get("warnings"), list):
    print("warnings must be a list", file=sys.stderr)
    sys.exit(1)
PY
}

assert_breakdown_totals() {
  local body="$1"
  local bucket_a_id="$2"
  local bucket_b_id="$3"
  REPORT_BODY="${body}" BUCKET_A="${bucket_a_id}" BUCKET_B="${bucket_b_id}" python3 - <<'PY'
import json, os, sys
body = os.environ.get("REPORT_BODY", "")
bucket_a = os.environ.get("BUCKET_A")
bucket_b = os.environ.get("BUCKET_B")
data = json.loads(body) if body else {}
msg = data.get("message") if isinstance(data.get("message"), dict) else data
rows = msg.get("data") or []
bucket_map = {row.get("bucket_id"): row for row in rows if isinstance(row, dict)}
a_total = float((bucket_map.get(bucket_a) or {}).get("total_amount") or 0)
b_total = float((bucket_map.get(bucket_b) or {}).get("total_amount") or 0)
total = a_total + b_total
if abs(a_total - 40.0) > 0.01:
    print(f"unexpected bucket A total: {a_total}", file=sys.stderr)
    sys.exit(1)
if abs(b_total - 60.0) > 0.01:
    print(f"unexpected bucket B total: {b_total}", file=sys.stderr)
    sys.exit(1)
if abs(total - 100.0) > 0.01:
    print(f"unexpected combined total: {total}", file=sys.stderr)
    sys.exit(1)
print(f"breakdown totals OK: {a_total:.2f}+{b_total:.2f}={total:.2f}")
PY
}

assert_cashflow_totals() {
  local body="$1"
  local bucket_a_id="$2"
  local bucket_b_id="$3"
  REPORT_BODY="${body}" BUCKET_A="${bucket_a_id}" BUCKET_B="${bucket_b_id}" python3 - <<'PY'
import json, os, sys
body = os.environ.get("REPORT_BODY", "")
bucket_a = os.environ.get("BUCKET_A")
bucket_b = os.environ.get("BUCKET_B")
data = json.loads(body) if body else {}
msg = data.get("message") if isinstance(data.get("message"), dict) else data
rows = msg.get("data") or []
a_total = 0.0
b_total = 0.0
for row in rows:
    if not isinstance(row, dict):
        continue
    bucket_id = row.get("bucket_id")
    amount = float(row.get("amount") or 0)
    if bucket_id == bucket_a:
        a_total += amount
    elif bucket_id == bucket_b:
        b_total += amount
total = a_total + b_total
if abs(a_total - 40.0) > 0.01:
    print(f"unexpected cashflow bucket A total: {a_total}", file=sys.stderr)
    sys.exit(1)
if abs(b_total - 60.0) > 0.01:
    print(f"unexpected cashflow bucket B total: {b_total}", file=sys.stderr)
    sys.exit(1)
if abs(total - 100.0) > 0.01:
    print(f"unexpected cashflow combined total: {total}", file=sys.stderr)
    sys.exit(1)
print(f"cashflow totals OK: {a_total:.2f}+{b_total:.2f}={total:.2f}")
PY
}

assert_pull_has_transaction_bucket() {
  local body="$1"
  local tx_id="$2"
  PULL_BODY="${body}" TX_ID="${tx_id}" python3 - <<'PY'
import json, os, sys
body = os.environ.get("PULL_BODY", "")
tx_id = os.environ.get("TX_ID")
data = json.loads(body) if body else {}
msg = data.get("message") if isinstance(data.get("message"), dict) else data
items = msg.get("items") or []
for item in items:
    if not isinstance(item, dict):
        continue
    if item.get("entity_type") != "Hisabi Transaction Bucket":
        continue
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
if any(
    (item.get("entity_type") == "Hisabi Transaction Bucket")
    and (((item.get("payload") or {}).get("transaction_id")) == tx_id)
    for item in items
):
    print("sync_pull contains transaction-bucket rows")
    sys.exit(0)
print("missing transaction-bucket rows in sync_pull", file=sys.stderr)
sys.exit(1)
PY
}

extract_sync_result_entity_id() {
  local body="$1"
  local entity_type="$2"
  local client_id="$3"
  SYNC_BODY="${body}" ENTITY_TYPE="${entity_type}" CLIENT_ID="${client_id}" python3 - <<'PY'
import json, os
body = os.environ.get("SYNC_BODY", "")
entity_type = os.environ.get("ENTITY_TYPE")
client_id = os.environ.get("CLIENT_ID")
data = json.loads(body) if body else {}
msg = data.get("message") if isinstance(data.get("message"), dict) else data
results = msg.get("results") or []
for row in results:
    if not isinstance(row, dict):
        continue
    if row.get("entity_type") != entity_type:
        continue
    if row.get("client_id") != client_id:
        continue
    entity_id = row.get("entity_id") or row.get("client_id")
    if entity_id:
        print(entity_id)
        raise SystemExit(0)
print("")
PY
}

TS=$(date +%s)
WALLET_CLIENT_ID="wallet-bucket-reports-${TS}-${RANDOM}"
ACCOUNT_ID="acc-bucket-reports-${TS}"
BUCKET_A_ID="bucket-a-reports-${TS}"
BUCKET_B_ID="bucket-b-reports-${TS}"
TX_ID="tx-bucket-reports-${TS}"

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Bucket Reports User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Bucket Reports Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
print_status_and_body "${REGISTER_RESP}"
require_http_status "${REGISTER_RESP}" "200"
REGISTER_BODY=$(response_body "${REGISTER_RESP}")

TOKEN=$(echo "${REGISTER_BODY}" | json_get message.auth.token || true)
if [[ -z "${TOKEN}" ]]; then
  echo "Missing auth token from register response" >&2
  echo "${REGISTER_BODY}" >&2
  exit 1
fi

echo "==> Create wallet"
WALLET_CREATE_PUSH_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_CLIENT_ID}",
  "items":[
    {
      "op_id":"op-wallet-create-${TS}",
      "entity_type":"Hisabi Wallet",
      "entity_id":"${WALLET_CLIENT_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${WALLET_CLIENT_ID}",
        "wallet_name":"Bucket Reports Wallet",
        "status":"active"
      }
    }
  ]
}
JSON
)
WALLET_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${WALLET_CREATE_PUSH_PAYLOAD}" "${TOKEN}")
print_status_and_body "${WALLET_RESP}"
require_http_status "${WALLET_RESP}" "200"
WALLET_BODY=$(response_body "${WALLET_RESP}")
WALLET_STATUS=$(echo "${WALLET_BODY}" | json_get message.results.0.status || true)
if [[ "${WALLET_STATUS}" != "accepted" && "${WALLET_STATUS}" != "duplicate" ]]; then
  echo "Wallet create via sync_push was not accepted" >&2
  echo "${WALLET_BODY}" >&2
  exit 1
fi
WALLET_ID="${WALLET_CLIENT_ID}"

echo "==> sync_push (account + buckets + transaction + split allocations)"
SYNC_PUSH_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {
      "op_id":"op-acc-${TS}",
      "entity_type":"Hisabi Account",
      "entity_id":"${ACCOUNT_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${ACCOUNT_ID}",
        "account_name":"Cash",
        "account_type":"cash",
        "currency":"SAR",
        "opening_balance":0
      }
    },
    {
      "op_id":"op-bucket-a-${TS}",
      "entity_type":"Hisabi Bucket",
      "entity_id":"${BUCKET_A_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${BUCKET_A_ID}",
        "title":"Essentials"
      }
    },
    {
      "op_id":"op-bucket-b-${TS}",
      "entity_type":"Hisabi Bucket",
      "entity_id":"${BUCKET_B_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${BUCKET_B_ID}",
        "title":"Savings"
      }
    },
    {
      "op_id":"op-tx-${TS}",
      "entity_type":"Hisabi Transaction",
      "entity_id":"${TX_ID}",
      "operation":"create",
      "payload":{
        "client_id":"${TX_ID}",
        "transaction_type":"income",
        "date_time":"$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
        "amount":100,
        "currency":"SAR",
        "account":"${ACCOUNT_ID}"
      }
    }
  ]
}
JSON
)
SYNC_PUSH_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${SYNC_PUSH_PAYLOAD}" "${TOKEN}")
print_status_and_body "${SYNC_PUSH_RESP}"
require_http_status "${SYNC_PUSH_RESP}" "200"
SYNC_PUSH_BODY=$(response_body "${SYNC_PUSH_RESP}")
PUSH_OK_COUNT=$(SYNC_BODY="${SYNC_PUSH_BODY}" python3 - <<'PY'
import json, os
body = os.environ.get("SYNC_BODY", "")
data = json.loads(body) if body else {}
msg = data.get("message") if isinstance(data.get("message"), dict) else data
results = msg.get("results") or []
ok = 0
for row in results:
    status = (row or {}).get("status")
    if status in {"accepted", "duplicate"}:
        ok += 1
print(ok)
PY
)
if [[ "${PUSH_OK_COUNT}" -lt 4 ]]; then
  echo "sync_push did not accept all records" >&2
  echo "${SYNC_PUSH_BODY}" >&2
  exit 1
fi
TX_NAME=$(extract_sync_result_entity_id "${SYNC_PUSH_BODY}" "Hisabi Transaction" "${TX_ID}")
if [[ -z "${TX_NAME}" ]]; then
  echo "Unable to resolve transaction entity_id from sync_push results" >&2
  echo "${SYNC_PUSH_BODY}" >&2
  exit 1
fi

echo "==> sync_pull (resolve transaction name)"
SINCE=$(date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%SZ')
SYNC_PULL_RESP=$(curl_get_with_status "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${TOKEN}" \
  --data-urlencode "device_id=${DEVICE_ID}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "since=${SINCE}" \
  --data-urlencode "limit=200")
print_status_and_body "${SYNC_PULL_RESP}"
require_http_status "${SYNC_PULL_RESP}" "200"
SYNC_PULL_BODY=$(response_body "${SYNC_PULL_RESP}")

echo "==> sync_push (create split transaction-bucket rows)"
ALLOC_PAYLOAD=$(cat <<JSON
{
  "device_id":"${DEVICE_ID}",
  "wallet_id":"${WALLET_ID}",
  "items":[
    {
      "op_id":"op-tb-a-${TS}",
      "entity_type":"Hisabi Transaction Bucket",
      "entity_id":"tb-a-reports-${TS}",
      "operation":"create",
      "payload":{
        "client_id":"tb-a-reports-${TS}",
        "transaction_id":"${TX_NAME}",
        "bucket_id":"${BUCKET_A_ID}",
        "amount":40,
        "percentage":40
      }
    },
    {
      "op_id":"op-tb-b-${TS}",
      "entity_type":"Hisabi Transaction Bucket",
      "entity_id":"tb-b-reports-${TS}",
      "operation":"create",
      "payload":{
        "client_id":"tb-b-reports-${TS}",
        "transaction_id":"${TX_NAME}",
        "bucket_id":"${BUCKET_B_ID}",
        "amount":60,
        "percentage":60
      }
    }
  ]
}
JSON
)
ALLOC_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ALLOC_PAYLOAD}" "${TOKEN}")
print_status_and_body "${ALLOC_RESP}"
require_http_status "${ALLOC_RESP}" "200"
ALLOC_BODY=$(response_body "${ALLOC_RESP}")
ALLOC_OK_COUNT=$(ALLOC_BODY="${ALLOC_BODY}" python3 - <<'PY'
import json, os
body = os.environ.get("ALLOC_BODY", "")
data = json.loads(body) if body else {}
msg = data.get("message") if isinstance(data.get("message"), dict) else data
results = msg.get("results") or []
ok = 0
for row in results:
    if (row or {}).get("status") in {"accepted", "duplicate"}:
        ok += 1
print(ok)
PY
)
if [[ "${ALLOC_OK_COUNT}" -lt 2 ]]; then
  echo "split transaction-bucket sync_push was not fully accepted" >&2
  echo "${ALLOC_BODY}" >&2
  exit 1
fi

echo "==> sync_pull (verify transaction-bucket visibility)"
SYNC_PULL_RESP=$(curl_get_with_status "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${TOKEN}" \
  --data-urlencode "device_id=${DEVICE_ID}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "since=${SINCE}" \
  --data-urlencode "limit=200")
print_status_and_body "${SYNC_PULL_RESP}"
require_http_status "${SYNC_PULL_RESP}" "200"
SYNC_PULL_BODY=$(response_body "${SYNC_PULL_RESP}")
assert_pull_has_transaction_bucket "${SYNC_PULL_BODY}" "${TX_NAME}"

echo "==> report_bucket_breakdown"
BREAKDOWN_RESP=$(curl_get_with_status "${BASE_URL}/api/method/hisabi_backend.api.v1.reports_finance.report_bucket_breakdown" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "currency=SAR")
print_status_and_body "${BREAKDOWN_RESP}"
require_http_status "${BREAKDOWN_RESP}" "200"
BREAKDOWN_BODY=$(response_body "${BREAKDOWN_RESP}")
assert_report_shape "${BREAKDOWN_BODY}"
assert_breakdown_totals "${BREAKDOWN_BODY}" "${BUCKET_A_ID}" "${BUCKET_B_ID}"

echo "==> report_cashflow_by_bucket"
CASHFLOW_RESP=$(curl_get_with_status "${BASE_URL}/api/method/hisabi_backend.api.v1.reports_finance.report_cashflow_by_bucket" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "currency=SAR")
print_status_and_body "${CASHFLOW_RESP}"
require_http_status "${CASHFLOW_RESP}" "200"
CASHFLOW_BODY=$(response_body "${CASHFLOW_RESP}")
assert_report_shape "${CASHFLOW_BODY}"
assert_cashflow_totals "${CASHFLOW_BODY}" "${BUCKET_A_ID}" "${BUCKET_B_ID}"

echo "Bucket reports verification OK."
