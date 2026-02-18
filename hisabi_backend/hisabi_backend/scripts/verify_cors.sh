#!/usr/bin/env bash
# Purpose: ad-hoc CORS preflight diagnostic for RC/production incidents.
# When to use: only when troubleshooting `allow_cors` behavior after deploy.
# Safety: sends read-only OPTIONS requests; does not mutate server data.
set -euo pipefail

BASE_URL=${BASE_URL:-"https://hisabi.yemenfrappe.com"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}

ENDPOINTS=(
  "/api/method/hisabi_backend.api.v1.me"
  "/api/method/hisabi_backend.api.v1.login"
  "/api/method/hisabi_backend.api.v1.register_user"
)

function header_count() {
  local header=$1
  echo "${RESPONSE_HEADERS}" | awk -v h="$header" 'BEGIN{IGNORECASE=1} $0 ~ "^"h":" {c++} END{print c+0}'
}

for endpoint in "${ENDPOINTS[@]}"; do
  method="GET"
  if [[ "${endpoint}" == *"/login" || "${endpoint}" == *"/register_user" ]]; then
    method="POST"
  fi

  RESPONSE_HEADERS=$(curl -s -i -X OPTIONS \
    "${BASE_URL}${endpoint}" \
    -H "Origin: ${ORIGIN}" \
    -H "Access-Control-Request-Method: ${method}" \
    -H "Access-Control-Request-Headers: Content-Type, Authorization" | tr -d '\r')

  echo "==> ${endpoint}"
  echo "${RESPONSE_HEADERS}" | head -n 20

  ACAO=$(header_count "Access-Control-Allow-Origin")
  ACAC=$(header_count "Access-Control-Allow-Credentials")
  VARY=$(header_count "Vary")

  if [[ "${ACAO}" -ne 1 || "${ACAC}" -ne 1 || "${VARY}" -ne 1 ]]; then
    echo "CORS header duplication detected for ${endpoint}:" >&2
    echo "Access-Control-Allow-Origin: ${ACAO}" >&2
    echo "Access-Control-Allow-Credentials: ${ACAC}" >&2
    echo "Vary: ${VARY}" >&2
    exit 1
  fi
done

echo "CORS headers OK (single-source) for all endpoints"
