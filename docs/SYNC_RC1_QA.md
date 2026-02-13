# SYNC RC1 QA (2026-02-13)

## What was fixed

### Phase A (backend unblock)
- Verified and enforced device wallet binding in auth endpoints:
  - `register_device` sets `Hisabi Device.wallet_id` via `ensure_default_wallet_for_user(...)`.
  - `link_device_to_user` sets `Hisabi Device.wallet_id` via `ensure_default_wallet_for_user(...)`.
- Added regression tests to guarantee device wallet binding is always present:
  - `test_register_device_sets_wallet_id`
  - `test_link_device_to_user_sets_wallet_id`

### Phase B (sync correctness)
- Backend sync contract hardening (`api/v1/sync.py`):
  - Added/confirmed allowlist coverage for `Hisabi Settings`, `Hisabi FX Rate`, `Hisabi Custom Currency`.
  - Added field mapping aliases for settings camelCase payloads (`phoneNumber`, `notificationsPreferences`, `enforceFx`).
  - Added sensitive field denylist (`password`, etc.) with explicit rejection (`sensitive_field_not_allowed`).
  - Added strict type validation for new optional settings fields:
    - `phone_number` must be string
    - `notifications_preferences` must be JSON (list/object)
    - `enforce_fx` must be number
- Backend tests extended in `hisabi_backend/tests/test_sync.py`:
  - Settings camelCase update acceptance
  - Sensitive password field rejection
  - Optional settings field type rejection
  - Wallet-scoped pull behavior for FX + transactions

### Verifier updates
- Extended `hisabi_backend/scripts/verify_sync_e2e.sh` with:
  - `test_sync_push_settings_update_accepts_camel_case_fields`
  - `test_sync_push_rejects_sensitive_password_field_in_payload`
  - `test_sync_push_rejects_invalid_settings_optional_field_types`
  - `test_sync_pull_enforces_wallet_scope_for_fx_and_transactions`

## Gate commands

### Backend gates
```bash
python3 -m py_compile \
  hisabi_backend/hisabi_backend/api/v1/sync.py \
  hisabi_backend/hisabi_backend/api/v1/auth.py \
  hisabi_backend/hisabi_backend/tests/test_sync.py \
  hisabi_backend/hisabi_backend/tests/test_auth.py

bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync
bash hisabi_backend/hisabi_backend/scripts/verify_sync_e2e.sh hisabi.yemenfrappe.com
```

### Focused Phase-A checks
```bash
bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_auth --test test_register_device_sets_wallet_id
bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_auth --test test_link_device_to_user_sets_wallet_id
```

## Scenarios proven
- Device registration/link always persists wallet binding (`wallet_id`) to `Hisabi Device`.
- Settings/FX/custom-currency sync payloads are accepted with wallet scope, while sensitive fields are rejected.
- Settings camelCase payload compatibility is preserved.
- Multi-entity push/pull path for settings + currencies + FX + accounts + categories + transactions remains green in sync tests.
- Pull is wallet-scoped for FX and transactions and rejects cross-wallet leakage.

## Known limitations
- In this environment, the full `hisabi_backend.tests.test_auth` module has a pre-existing unrelated failure in `test_register_and_login_with_phone` (phone normalization mismatch in test data generation).
- In this environment, `hisabi_backend.tests.test_auth_v2` has multiple pre-existing failures related to request context/token expectations; this change set did not modify `auth_v2` code paths.
