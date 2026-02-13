#!/usr/bin/env bash
set -euo pipefail

SITE="${1:-${SITE:-hisabi.yemenfrappe.com}}"
MODULE="hisabi_backend.tests.test_sync"

echo "==> Running sync e2e checks on site: ${SITE}"
bench --site "${SITE}" run-tests --module "${MODULE}" --test test_sync_push_persists_settings_currency_fx_accounts_categories_transactions
bench --site "${SITE}" run-tests --module "${MODULE}" --test test_sync_push_settings_update_accepts_camel_case_fields
bench --site "${SITE}" run-tests --module "${MODULE}" --test test_sync_push_rejects_sensitive_password_field_in_payload
bench --site "${SITE}" run-tests --module "${MODULE}" --test test_sync_push_rejects_invalid_settings_optional_field_types
bench --site "${SITE}" run-tests --module "${MODULE}" --test test_sync_pull_enforces_wallet_scope_for_fx_and_transactions
echo "==> Sync e2e checks passed"
