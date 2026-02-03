#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
ORIGIN=${ORIGIN:-"https://hisabi.yemenfrappe.com"}
ENDPOINT=${ENDPOINT:-"/api/method/hisabi_backend.api.v1.register_user"}

function header_count() {
  local header=$1
  echo "${RESPONSE_HEADERS}" | awk -v h="$header" 'BEGIN{IGNORECASE=1} $0 ~ "^"h":" {c++} END{print c+0}'
}

RESPONSE_HEADERS=$(curl -s -i -X OPTIONS \
  "${BASE_URL}${ENDPOINT}" \
  -H "Origin: ${ORIGIN}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type, Authorization" | tr -d '\r')

echo "${RESPONSE_HEADERS}" | head -n 20

ACAO=$(header_count "Access-Control-Allow-Origin")
ACAC=$(header_count "Access-Control-Allow-Credentials")
VARY=$(header_count "Vary")

if [[ "${ACAO}" -ne 1 || "${ACAC}" -ne 1 || "${VARY}" -ne 1 ]]; then
  echo "CORS header duplication detected:" >&2
  echo "Access-Control-Allow-Origin: ${ACAO}" >&2
  echo "Access-Control-Allow-Credentials: ${ACAC}" >&2
  echo "Vary: ${VARY}" >&2
  exit 1
fi

echo "CORS headers OK (single-source)"
