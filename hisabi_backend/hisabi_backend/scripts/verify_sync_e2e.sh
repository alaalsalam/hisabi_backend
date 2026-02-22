#!/usr/bin/env bash
set -euo pipefail

SITE="${1:-${SITE:-hisabi.yemenfrappe.com}}"
MODULE="hisabi_backend.tests.test_sync"

run_test() {
    local test_name="$1"
    local tmp_log
    tmp_log="$(mktemp)"

    echo "==> ${test_name}"
    if ! bench --site "${SITE}" run-tests --module "${MODULE}" --test "${test_name}" 2>&1 | tee "${tmp_log}"; then
        rm -f "${tmp_log}"
        return 1
    fi

    if grep -Eiq "(^FAIL:|^FAIL$|^FAILED|^ERROR:|Traceback \\(most recent call last\\):)" "${tmp_log}"; then
        echo "==> Detected failing test output for ${test_name}" >&2
        rm -f "${tmp_log}"
        return 1
    fi

    rm -f "${tmp_log}"
}

echo "==> Running sync e2e checks on site: ${SITE}"
run_test test_sync_push_persists_settings_currency_fx_accounts_categories_transactions
run_test test_sync_push_settings_update_accepts_camel_case_fields
run_test test_sync_push_rejects_sensitive_password_field_in_payload
run_test test_sync_push_rejects_invalid_settings_optional_field_types
run_test test_sync_pull_enforces_wallet_scope_for_fx_and_transactions
echo "==> Sync e2e checks passed"
