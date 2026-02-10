#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# WHY: Provide a localhost-only release gate wrapper for DNS-restricted runners.
# WHEN: Use in CI/dev environments where production DNS/egress is blocked.
# SAFETY: Runs existing verification scripts unchanged with local defaults only.
# ------------------------------------------------------------------------------
set -euo pipefail

BASE_URL=${BASE_URL:-"http://127.0.0.1:18000"}
ORIGIN=${ORIGIN:-"http://localhost:8082"}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

mask_token() {
  local token="${1:-}"
  local len=${#token}
  if [[ ${len} -le 10 ]]; then
    printf '***'
    return
  fi
  printf '%s...%s' "${token:0:6}" "${token: -4}"
}

run_gate() {
  local script_name="$1"
  local out_file
  out_file=$(mktemp)
  echo "==> RUN ${script_name}"
  if BASE_URL="${BASE_URL}" ORIGIN="${ORIGIN}" bash "${SCRIPT_DIR}/${script_name}" >"${out_file}" 2>&1; then
    sed -E \
      -e 's/(\"token\"[[:space:]]*:[[:space:]]*\")[^\"]+/\1***MASKED***/g' \
      -e 's/\bhisabi_[A-Za-z0-9._-]+/***MASKED***/g' \
      "${out_file}"
  else
    sed -E \
      -e 's/(\"token\"[[:space:]]*:[[:space:]]*\")[^\"]+/\1***MASKED***/g' \
      -e 's/\bhisabi_[A-Za-z0-9._-]+/***MASKED***/g' \
      "${out_file}"
    rm -f "${out_file}"
    echo "==> FAIL ${script_name}" >&2
    exit 1
  fi
  rm -f "${out_file}"
  echo "==> PASS ${script_name}"
}

echo "==> Local gate suite starting"
echo "BASE_URL=${BASE_URL}"
echo "ORIGIN=${ORIGIN}"
if [[ -n "${HISABI_TOKEN:-}" ]]; then
  echo "HISABI_TOKEN_MASKED=$(mask_token "${HISABI_TOKEN}")"
fi

run_gate "verify_auth_smoke.sh"
run_gate "verify_sync_pull.sh"
run_gate "verify_sync_push_e2e.sh"
run_gate "verify_sync_conflict_resolution.sh"
run_gate "verify_bucket_reports.sh"
run_gate "verify_recurring.sh"
run_gate "verify_backup_restore.sh"

DIAG_URL="${BASE_URL}/api/method/hisabi_backend.api.v1.health.diag"
echo "==> RUN health diag ${DIAG_URL}"
DIAG_RAW=$(curl -sS "${DIAG_URL}")
if command -v jq >/dev/null 2>&1; then
  echo "${DIAG_RAW}" | jq .
else
  echo "${DIAG_RAW}"
fi

echo "LOCAL GATE SUITE PASS"
