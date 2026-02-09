#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Validate reports API contract stability for release gates.
# WHEN: Run before/after deploys touching reports, filters, or wallet scoping.
# SAFETY: Uses a throwaway generated user/device; no destructive operations.
# ------------------------------------------------------------------------------
set -euo pipefail

BASE_URL=${BASE_URL:-"https://expense.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-reports-contract-$(date +%s)"}

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

require_http_status() {
  local response="$1"
  local expected="$2"
  local actual
  actual=$(http_status "${response}")
  if [[ "${actual}" != "${expected}" ]]; then
    echo "Expected HTTP ${expected}, got ${actual}" >&2
    print_status_and_body "${response}" >&2
    exit 1
  fi
}

require_message_keys() {
  local body="$1"
  shift
  local keys=("$@")
  local missing
  missing=$(REPORT_BODY="${body}" KEYS="${keys[*]}" python3 - <<'PY'
import json, os
body = os.environ.get("REPORT_BODY", "")
keys = [k for k in os.environ.get("KEYS", "").split(" ") if k]
missing = []
try:
    data = json.loads(body) if body else {}
except json.JSONDecodeError:
    print("invalid_json")
    raise SystemExit
msg = data.get("message") if isinstance(data.get("message"), dict) else data
for key in keys:
    if key not in msg:
        missing.append(key)
print(",".join(missing))
PY
)
  if [[ "${missing}" == "invalid_json" || -n "${missing}" ]]; then
    echo "Missing required keys: ${missing}" >&2
    echo "${body}" >&2
    exit 1
  fi
}

require_invalid_request_422() {
  local endpoint="$1"
  local response
  response=$(curl_get_with_status "${BASE_URL}${endpoint}" "${TOKEN}")
  print_status_and_body "${response}"
  require_http_status "${response}" "422"
  local body
  body=$(response_body "${response}")
  local code
  code=$(echo "${body}" | json_get error.code || true)
  if [[ "${code}" != "invalid_request" ]]; then
    echo "Expected error.code=invalid_request for ${endpoint}, got '${code}'" >&2
    echo "${body}" >&2
    exit 1
  fi
}

call_and_assert_report() {
  local endpoint="$1"
  local require_keys="$2"
  shift 2 || true
  local response
  response=$(curl_get_with_status "${BASE_URL}${endpoint}" "${TOKEN}" "$@")
  print_status_and_body "${response}"
  require_http_status "${response}" "200"
  local body
  body=$(response_body "${response}")
  # Contract invariant: reports expose warnings array and stable top-level payload keys.
  require_message_keys "${body}" ${require_keys} warnings server_time
}

DATE_FROM=$(date -u -d '30 days ago' '+%Y-%m-%d')
DATE_TO=$(date -u '+%Y-%m-%d')

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Reports User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Reports Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
print_status_and_body "${REGISTER_RESP}"
require_http_status "${REGISTER_RESP}" "200"
REGISTER_BODY=$(response_body "${REGISTER_RESP}")

TOKEN=$(echo "${REGISTER_BODY}" | json_get message.auth.token || true)
WALLET_ID=$(echo "${REGISTER_BODY}" | json_get message.default_wallet_id || true)
if [[ -z "${TOKEN}" || -z "${WALLET_ID}" ]]; then
  echo "Missing token or wallet_id in register response" >&2
  echo "${REGISTER_BODY}" >&2
  exit 1
fi

echo "==> report_summary (minimal params)"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_summary" \
  "accounts totals budgets goals debts jameya_upcoming" \
  --data-urlencode "wallet_id=${WALLET_ID}"

echo "==> report_summary (date range + filters)"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_summary" \
  "accounts totals budgets goals debts jameya_upcoming" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "date_from=${DATE_FROM}" \
  --data-urlencode "date_to=${DATE_TO}" \
  --data-urlencode "type=expense"

echo "==> report_cashflow (minimal + date range)"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_cashflow" \
  "points totals" \
  --data-urlencode "wallet_id=${WALLET_ID}"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_cashflow" \
  "points totals" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "date_from=${DATE_FROM}" \
  --data-urlencode "date_to=${DATE_TO}"

echo "==> report_category_breakdown (minimal + date range)"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_category_breakdown" \
  "categories totals" \
  --data-urlencode "wallet_id=${WALLET_ID}"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_category_breakdown" \
  "categories totals" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "date_from=${DATE_FROM}" \
  --data-urlencode "date_to=${DATE_TO}"

echo "==> report_trends (daily + weekly)"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_trends" \
  "granularity points totals" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "granularity=daily"
call_and_assert_report \
  "/api/method/hisabi_backend.api.v1.reports_finance.report_trends" \
  "granularity points totals" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "date_from=${DATE_FROM}" \
  --data-urlencode "date_to=${DATE_TO}" \
  --data-urlencode "granularity=weekly"

echo "==> missing wallet_id should be 422 invalid_request"
require_invalid_request_422 "/api/method/hisabi_backend.api.v1.reports_finance.report_summary"
require_invalid_request_422 "/api/method/hisabi_backend.api.v1.reports_finance.report_cashflow"
require_invalid_request_422 "/api/method/hisabi_backend.api.v1.reports_finance.report_category_breakdown"
require_invalid_request_422 "/api/method/hisabi_backend.api.v1.reports_finance.report_trends"

echo "Reports contract verification OK."
