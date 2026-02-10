#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Validate Sprint 09 backup/restore export + dry-run + idempotent apply.
# WHEN: Run after backup/restore backend changes.
# SAFETY: Uses generated auth and wallet-scoped merge operations only.
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
  local token=$3
  curl -sS -X POST "${BASE_URL}${endpoint}" \
    -H 'Content-Type: application/json' \
    -H "Origin: ${ORIGIN}" \
    -H "Authorization: Bearer ${token}" \
    -d "${payload}"
}

post_form() {
  local endpoint=$1
  local token=$2
  shift 2
  curl -sS -X POST "${BASE_URL}${endpoint}" \
    -H "Origin: ${ORIGIN}" \
    -H "Authorization: Bearer ${token}" \
    "$@"
}

get_json() {
  local endpoint=$1
  local token=$2
  curl -sS -G "${BASE_URL}${endpoint}" \
    -H "Origin: ${ORIGIN}" \
    -H "Authorization: Bearer ${token}"
}

count_entities() {
  jq -r '(.entities // {}) | to_entries | map(.value | length) | add // 0'
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
if [[ -z "${WALLET_ID}" ]]; then
  echo "failed to resolve wallet_id from /me" >&2
  echo "${ME_RESP}" >&2
  exit 1
fi

echo "==> Export backup snapshot"
EXPORT_RAW=$(post_form "/api/method/hisabi_backend.api.v1.backup_export" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "format=hisabi_json_v1")
EXPORT_PAYLOAD=$(echo "${EXPORT_RAW}" | jq -c '.message // .')
FMT=$(echo "${EXPORT_PAYLOAD}" | jq -r '.meta.format // empty')
if [[ "${FMT}" != "hisabi_json_v1" ]]; then
  echo "backup export format mismatch" >&2
  echo "${EXPORT_RAW}" >&2
  exit 1
fi
BASELINE_TOTAL=$(echo "${EXPORT_PAYLOAD}" | count_entities)

echo "==> Validate restore dry-run"
VALIDATE_RESP=$(post_form "/api/method/hisabi_backend.api.v1.backup_validate_restore" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "payload=${EXPORT_PAYLOAD}")
if [[ "$(echo "${VALIDATE_RESP}" | jq -r '.message.status // empty')" != "ok" ]]; then
  echo "validate_restore failed" >&2
  echo "${VALIDATE_RESP}" >&2
  exit 1
fi

echo "==> Apply restore (1st)"
APPLY_FIRST=$(post_form "/api/method/hisabi_backend.api.v1.backup_apply_restore" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "payload=${EXPORT_PAYLOAD}" \
  --data-urlencode "mode=merge")
if [[ "$(echo "${APPLY_FIRST}" | jq -r '.message.status // empty')" != "ok" ]]; then
  echo "first apply_restore failed" >&2
  echo "${APPLY_FIRST}" >&2
  exit 1
fi

echo "==> Apply restore (2nd idempotent)"
APPLY_SECOND=$(post_form "/api/method/hisabi_backend.api.v1.backup_apply_restore" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "payload=${EXPORT_PAYLOAD}" \
  --data-urlencode "mode=merge")
if [[ "$(echo "${APPLY_SECOND}" | jq -r '.message.status // empty')" != "ok" ]]; then
  echo "second apply_restore failed" >&2
  echo "${APPLY_SECOND}" >&2
  exit 1
fi

echo "==> Re-export and verify total entity count stable"
POST_EXPORT_RAW=$(post_form "/api/method/hisabi_backend.api.v1.backup_export" "${TOKEN}" \
  --data-urlencode "wallet_id=${WALLET_ID}" \
  --data-urlencode "format=hisabi_json_v1")
POST_EXPORT_PAYLOAD=$(echo "${POST_EXPORT_RAW}" | jq -c '.message // .')
POST_TOTAL=$(echo "${POST_EXPORT_PAYLOAD}" | count_entities)
if [[ "${POST_TOTAL}" != "${BASELINE_TOTAL}" ]]; then
  echo "entity totals changed after idempotent apply: baseline=${BASELINE_TOTAL} after=${POST_TOTAL}" >&2
  exit 1
fi

echo "PASS: verify_backup_restore"
