#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-${1:-}}
if [[ -z "${BASE_URL}" ]]; then
  echo "BASE_URL is required (env or first argument)" >&2
  exit 1
fi

EPOCH=$(date +%s)
PHONE=${PHONE:-"+9677${EPOCH: -8}"}
PASSWORD=${PASSWORD:-"Verify123!@#"}
FULL_NAME=${FULL_NAME:-"Gate Verifier"}
DEVICE_ID=${DEVICE_ID:-"dev-gate-${EPOCH}"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}

json_get() {
  local path="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -er "${path}" 2>/dev/null
  else
    python3 -c $'import json,sys\npath=sys.argv[1].split(".")\nraw=sys.stdin.read()\ndata=json.loads(raw)\nfor k in path:\n    if isinstance(data, dict):\n        data=data.get(k)\n    elif isinstance(data, list) and k.isdigit():\n        i=int(k)\n        data=data[i] if 0 <= i < len(data) else None\n    else:\n        data=None\n    if data is None:\n        break\nif data is None:\n    raise SystemExit(1)\nprint(data)\n' "${path}"
  fi
}

mask_token() {
  local token="${1:-}"
  local len=${#token}
  if [[ ${len} -le 10 ]]; then
    printf '***'
    return
  fi
  printf '%s...%s' "${token:0:6}" "${token: -4}"
}

echo "==> Preflight OPTIONS" >&2
curl -sS -i -X OPTIONS \
  "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" \
  -H "Origin: ${ORIGIN}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type, Authorization" | head -n 5 >&2

REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"${FULL_NAME}","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Gate Verifier Device"}}
JSON
)

echo "==> Register user for verifier token" >&2
REGISTER_RESP=$(curl -sS -X POST \
  "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" \
  -H "Content-Type: application/json" \
  -d "${REGISTER_PAYLOAD}")

TOKEN=$(echo "${REGISTER_RESP}" | json_get ".message.auth.token" || true)
if [[ -z "${TOKEN}" ]]; then
  echo "Failed to mint token from register response" >&2
  echo "${REGISTER_RESP}" >&2
  exit 1
fi

echo "MASKED_TOKEN=$(mask_token "${TOKEN}")" >&2

printf 'export HISABI_BASE_URL=%q\n' "${BASE_URL}"
printf 'export HISABI_TOKEN=%q\n' "${TOKEN}"
