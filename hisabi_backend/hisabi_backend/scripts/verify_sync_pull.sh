#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-sync-pull-$(date +%s)"}

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

function curl_with_status_get() {
  local url=$1
  local token=${2:-}
  shift 2 || true
  if [[ -n "${token}" ]]; then
    curl -s -i -G -w "\nHTTP_STATUS:%{http_code}\n" "${url}" \
      -H "Authorization: Bearer ${token}" \
      "$@"
  else
    curl -s -i -G -w "\nHTTP_STATUS:%{http_code}\n" "${url}" \
      "$@"
  fi
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

function assert_status() {
  local response="$1"
  local expected="$2"
  local status
  status=$(echo "${response}" | sed -n 's/HTTP_STATUS://p' | tail -n 1)
  if [[ "${status}" != "${expected}" ]]; then
    echo "Expected HTTP ${expected}, got HTTP ${status}" >&2
    exit 1
  fi
}

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify Sync Pull User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Sync Pull Device"}}
JSON
)
REGISTER_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" "${REGISTER_PAYLOAD}")
print_status_and_body "${REGISTER_RESP}"

TOKEN=$(echo "${REGISTER_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.auth.token || true)
if [[ -z "${TOKEN}" ]]; then
  echo "Missing token in register response" >&2
  exit 1
fi

echo "==> Me"
ME_RESP=$(curl_with_status GET "${BASE_URL}/api/method/hisabi_backend.api.v1.me" "" "${TOKEN}")
print_status_and_body "${ME_RESP}"

WALLET_ID=$(echo "${ME_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.default_wallet_id || true)
if [[ -z "${WALLET_ID}" ]]; then
  echo "Missing wallet id in me response" >&2
  exit 1
fi

TS=$(date +%s)
ACC_ID="acc-pull-${TS}"

echo "==> Sync push (create account)"
CREATE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-${TS}","entity_type":"Hisabi Account","entity_id":"${ACC_ID}","operation":"create","payload":{"client_id":"${ACC_ID}","name":"Pull Cash","type":"cash","currency":"SAR"}}
]}
JSON
)
CREATE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${CREATE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${CREATE_RESP}"
assert_status "${CREATE_RESP}" "200"

DOC_VERSION=$(echo "${CREATE_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.results.0.doc_version || true)
if [[ -z "${DOC_VERSION}" ]]; then
  echo "Missing doc_version in create response" >&2
  exit 1
fi

SINCE=$(date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%SZ')

echo "==> Sync pull (GET with query params)"
PULL_GET_RESP=$(curl_with_status_get "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${TOKEN}" \
  --data-urlencode "device_id=${DEVICE_ID}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "since=${SINCE}" \
  --data-urlencode "limit=50")
print_status_and_body "${PULL_GET_RESP}"
assert_status "${PULL_GET_RESP}" "200"

PULL_GET_BODY=$(response_body "${PULL_GET_RESP}")
HAS_ITEMS_FIELD=$(PULL_GET_BODY="${PULL_GET_BODY}" python3 - <<'PY'
import json,os
raw=os.environ.get("PULL_GET_BODY","")
data=json.loads(raw) if raw else {}
msg=data.get("message") or {}
print("yes" if isinstance(msg.get("items"), list) else "no")
PY
)
if [[ "${HAS_ITEMS_FIELD}" != "yes" ]]; then
  echo "missing message.items array in pull response" >&2
  exit 1
fi

PULL_BODY="${PULL_GET_BODY}"
HAS_ITEM=$(ACC_ID="${ACC_ID}" PULL_BODY="${PULL_BODY}" python3 - <<PY
import json,os
raw=os.environ.get("PULL_BODY","")
data=json.loads(raw) if raw else {}
items=(data.get("message") or {}).get("items") or []
acc=os.environ.get("ACC_ID")
found=any((i.get("entity_type") == "Hisabi Account" and (i.get("client_id") == acc or i.get("entity_id") == acc)) for i in items)
print("yes" if found else "no")
PY
)
if [[ "${HAS_ITEM}" != "yes" ]]; then
  echo "Expected account ${ACC_ID} in pull response" >&2
  exit 1
fi

if [[ "${VERIFY_PULL_ONLY:-1}" == "1" ]]; then
  echo "Done."
  exit 0
fi

NEXT_CURSOR=$(echo "${PULL_BODY}" | json_get message.next_cursor || true)
if [[ -z "${NEXT_CURSOR}" ]]; then
  echo "Missing next_cursor in pull response" >&2
  exit 1
fi

PULL_DOC_VERSION=$(ACC_ID="${ACC_ID}" PULL_BODY="${PULL_BODY}" python3 - <<PY
import json,os
raw=os.environ.get("PULL_BODY","")
data=json.loads(raw) if raw else {}
items=(data.get("message") or {}).get("items") or []
acc=os.environ.get("ACC_ID")
for item in items:
    if item.get("entity_type") != "Hisabi Account":
        continue
    if item.get("client_id") == acc or item.get("entity_id") == acc:
        val=item.get("doc_version")
        if val is not None:
            print(val)
            raise SystemExit
print("")
PY
)
if [[ -n "${PULL_DOC_VERSION}" ]]; then
  DOC_VERSION="${PULL_DOC_VERSION}"
fi

DELETE_ID=$(ACC_ID="${ACC_ID}" PULL_BODY="${PULL_BODY}" python3 - <<PY
import json,os
raw=os.environ.get("PULL_BODY","")
data=json.loads(raw) if raw else {}
items=(data.get("message") or {}).get("items") or []
acc=os.environ.get("ACC_ID")
for item in items:
    if item.get("entity_type") != "Hisabi Account":
        continue
    if item.get("client_id") == acc or item.get("entity_id") == acc:
        payload=item.get("payload") or {}
        cid=payload.get("client_id") or item.get("client_id") or item.get("entity_id")
        if cid:
            print(cid)
            raise SystemExit
        name=payload.get("name")
        if name:
            print(name)
            raise SystemExit
print(acc)
PY
)

echo "==> Sync push (delete account)"
DELETE_PAYLOAD=$(cat <<JSON
{"device_id":"${DEVICE_ID}","wallet_id":"${WALLET_ID}","items":[
  {"op_id":"op-acc-del-${TS}","entity_type":"Hisabi Account","entity_id":"${DELETE_ID}","operation":"delete","base_version":${DOC_VERSION},"payload":{"client_id":"${DELETE_ID}"}}
]}
JSON
)
DELETE_RESP=$(curl_with_status POST "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_push" "${DELETE_PAYLOAD}" "${TOKEN}")
print_status_and_body "${DELETE_RESP}"
assert_status "${DELETE_RESP}" "200"

DELETE_STATUS=$(echo "${DELETE_RESP}" | sed '/HTTP_STATUS:/d' | json_get message.results.0.status || true)
if [[ "${DELETE_STATUS}" != "accepted" && "${DELETE_STATUS}" != "duplicate" ]]; then
  echo "Delete did not succeed (status=${DELETE_STATUS})" >&2
  exit 1
fi

echo "==> Sync pull (after delete)"
PULL_AFTER_RESP=$(curl_with_status_get "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${TOKEN}" \
  --data-urlencode "device_id=${DEVICE_ID}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "cursor=${NEXT_CURSOR}" \
  --data-urlencode "limit=50")
print_status_and_body "${PULL_AFTER_RESP}"
assert_status "${PULL_AFTER_RESP}" "200"

PULL_AFTER_BODY=$(response_body "${PULL_AFTER_RESP}")
HAS_DELETE=$(ACC_ID="${DELETE_ID}" PULL_AFTER_BODY="${PULL_AFTER_BODY}" python3 - <<PY
import json,os
raw=os.environ.get("PULL_AFTER_BODY","")
data=json.loads(raw) if raw else {}
items=(data.get("message") or {}).get("items") or []
acc=os.environ.get("ACC_ID")
for item in items:
    if item.get("entity_type") != "Hisabi Account":
        continue
    if item.get("client_id") != acc and item.get("entity_id") != acc:
        continue
    if item.get("is_deleted") and item.get("deleted_at"):
        print("yes")
        raise SystemExit
print("no")
PY
)
if [[ "${HAS_DELETE}" != "yes" ]]; then
  echo "Expected is_deleted + deleted_at for ${ACC_ID} in pull response" >&2
  exit 1
fi

NEXT_CURSOR_AFTER=$(echo "${PULL_AFTER_BODY}" | json_get message.next_cursor || true)
if [[ -z "${NEXT_CURSOR_AFTER}" ]]; then
  echo "Missing next_cursor after delete pull response" >&2
  exit 1
fi

echo "==> Sync pull (repeat cursor, expect empty)"
PULL_REPEAT_RESP=$(curl_with_status_get "${BASE_URL}/api/method/hisabi_backend.api.v1.sync.sync_pull" "${TOKEN}" \
  --data-urlencode "device_id=${DEVICE_ID}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "cursor=${NEXT_CURSOR_AFTER}" \
  --data-urlencode "limit=50")
print_status_and_body "${PULL_REPEAT_RESP}"
assert_status "${PULL_REPEAT_RESP}" "200"

PULL_REPEAT_BODY=$(response_body "${PULL_REPEAT_RESP}")
HAS_ANY_ITEMS=$(PULL_REPEAT_BODY="${PULL_REPEAT_BODY}" python3 - <<'PY'
import json,os
raw=os.environ.get("PULL_REPEAT_BODY","")
data=json.loads(raw) if raw else {}
items=(data.get("message") or {}).get("items") or []
print("yes" if items else "no")
PY
)
if [[ "${HAS_ANY_ITEMS}" != "no" ]]; then
  echo "Expected empty items on repeat cursor pull" >&2
  exit 1
fi

echo "Done."
