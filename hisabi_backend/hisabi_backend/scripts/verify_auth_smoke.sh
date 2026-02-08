#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://expense.yemenfrappe.com"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}
UNIQUE_SUFFIX=${UNIQUE_SUFFIX:-"$(date +%s)-$RANDOM"}
PASSWORD=${PASSWORD:-"Test1234!"}
DEVICE_ID=${DEVICE_ID:-"dev-${UNIQUE_SUFFIX}"}

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

function require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for this script" >&2
    exit 1
  fi
}

function json_get() {
  local path=$1
  jq -er "${path}" 2>/dev/null
}

require_jq

echo "==> Preflight OPTIONS"
curl -s -i -X OPTIONS \
  "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" \
  -H "Origin: ${ORIGIN}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type, Authorization" | head -n 5

echo "==> Register user"
REGISTER_PAYLOAD=$(cat <<JSON
{"phone":"${PHONE}","full_name":"Verify User","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android","device_name":"Verify Device"}}
JSON
)
REGISTER_RESP=$(curl -s -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.register_user" \
  -H "Content-Type: application/json" \
  -d "${REGISTER_PAYLOAD}")

if ! echo "${REGISTER_RESP}" | jq -e . >/dev/null 2>&1; then
  echo "Register response is not valid JSON" >&2
  echo "${REGISTER_RESP}" >&2
  exit 1
fi

REGISTER_TOKEN=$(echo "${REGISTER_RESP}" | json_get '.message.auth.token')
REGISTER_WALLET_ID=$(echo "${REGISTER_RESP}" | json_get '.message.default_wallet_id')
REGISTER_DEVICE_ID=$(echo "${REGISTER_RESP}" | json_get '.message.device.device_id')

if [[ -z "${REGISTER_TOKEN}" || -z "${REGISTER_WALLET_ID}" || -z "${REGISTER_DEVICE_ID}" ]]; then
  echo "Missing token or default_wallet_id or device_id in register response" >&2
  echo "Register response: ${REGISTER_RESP}" >&2
  exit 1
fi

echo "Register OK: token_len=${#REGISTER_TOKEN} wallet_id=${REGISTER_WALLET_ID} device_id=${REGISTER_DEVICE_ID}"

echo "==> Login"
LOGIN_PAYLOAD=$(cat <<JSON
{"identifier":"${PHONE}","password":"${PASSWORD}","device":{"device_id":"${DEVICE_ID}","platform":"android"}}
JSON
)
LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/api/method/hisabi_backend.api.v1.login" \
  -H "Content-Type: application/json" \
  -d "${LOGIN_PAYLOAD}")

if ! echo "${LOGIN_RESP}" | jq -e . >/dev/null 2>&1; then
  echo "Login response is not valid JSON" >&2
  echo "${LOGIN_RESP}" >&2
  exit 1
fi

LOGIN_TOKEN=$(echo "${LOGIN_RESP}" | json_get '.message.auth.token')

if [[ -z "${LOGIN_TOKEN}" ]]; then
  echo "Missing auth.token in login response" >&2
  echo "Login response: ${LOGIN_RESP}" >&2
  exit 1
fi

echo "==> Me"
ME_RESP=$(curl -s -X GET "${BASE_URL}/api/method/hisabi_backend.api.v1.me" \
  -H "Authorization: Bearer ${LOGIN_TOKEN}")

if ! echo "${ME_RESP}" | jq -e . >/dev/null 2>&1; then
  echo "Me response is not valid JSON" >&2
  echo "${ME_RESP}" >&2
  exit 1
fi

echo "Me response: ${ME_RESP}" | head -c 200
echo ""

ME_WALLET_ID=$(echo "${ME_RESP}" | json_get '.message.default_wallet_id')
if [[ -z "${ME_WALLET_ID}" ]]; then
  echo "Missing default_wallet_id in me response" >&2
  echo "Me response: ${ME_RESP}" >&2
  exit 1
fi

echo "Smoke test OK: token_len=${#LOGIN_TOKEN} wallet_id=${ME_WALLET_ID}"
