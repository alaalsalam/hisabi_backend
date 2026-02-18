#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-fx-sanity-$(date +%s)"}

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

echo "==> Register user for FX sanity"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify FX Sanity","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify FX Sanity Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
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
ACCOUNT_ID="acc-fx-sanity-${TS}"
TX_ID="tx-fx-sanity-${TS}"
TX_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "==> Create source account (SAR)"
ACCOUNT_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[{"op_id":"op-fx-account-${TS}","entity_type":"Hisabi Account","entity_id":"${ACCOUNT_ID}","operation":"create","payload":{"client_id":"${ACCOUNT_ID}","name":"FX Sanity Account","type":"cash","currency":"SAR"}}]}
JSON
)
ACCOUNT_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${ACCOUNT_PAYLOAD}" "${TOKEN}")
require_http_200 "${ACCOUNT_RESP}" "account create failed"

# Intentionally omit fx_rate/fx_rate_used while using mismatched currency (YER -> SAR).
echo "==> Push transaction with currency mismatch and missing fx_rate_used"
TX_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[{"op_id":"op-fx-tx-${TS}","entity_type":"Hisabi Transaction","entity_id":"${TX_ID}","operation":"create","payload":{"client_id":"${TX_ID}","type":"expense","date_time":"${TX_DATE}","amount":100,"currency":"YER","account":"${ACCOUNT_ID}","note":"fx sanity check"}}]}
JSON
)
TX_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${TX_PAYLOAD}" "${TOKEN}")
require_http_200 "${TX_RESP}" "transaction create failed"
TX_BODY=$(response_body "${TX_RESP}")

ITEM_STATUS=$(echo "${TX_BODY}" | json_get message.results.0.status || true)
if [[ "${ITEM_STATUS}" != "accepted" ]]; then
  echo "Expected accepted status for non-blocking FX sanity check" >&2
  echo "${TX_BODY}" >&2
  exit 1
fi

WARNING_FOUND=$(TX_BODY="${TX_BODY}" python3 - <<'PY'
import json
import os
body = os.environ.get("TX_BODY", "")
try:
    data = json.loads(body) if body else {}
except json.JSONDecodeError:
    data = {}
result = ((data.get("message") or {}).get("results") or [{}])[0]
warnings = result.get("warnings") or []
print("yes" if "fx_rate_non_positive_for_currency_mismatch" in warnings else "no")
PY
)

if [[ "${WARNING_FOUND}" != "yes" ]]; then
  echo "Expected FX sanity warning in sync_push result" >&2
  echo "${TX_BODY}" >&2
  exit 1
fi

echo "FX_SANITY_WARNING_EVIDENCE: fx_rate_non_positive_for_currency_mismatch"
echo "PASS: verify_fx_sanity"
