# SYNC RC1 QA (v1 Stable RC)

Date: 2026-02-13

## What was fixed

### Phase A (`98e89f8`)
- Fixed legacy `api/v1/auth.py` device registration/link flows to always set `Hisabi Device.wallet_id` via `ensure_default_wallet_for_user(...)` before save.
- Returned `wallet_id` in `register_device` and `link_device_to_user` responses (additive, contract-safe).
- Switched legacy auth token issuance to v2-compatible bearer token format and preserved backward hash field.
- Added `test_sync.py` setup assertion to verify `register_device` persists `wallet_id`.
- Added defensive int32 clamping for device `*_ms` fields in auth path.

### Phase B backend (`d863c6f`)
- Enabled sync push coverage for wallet-scoped:
  - `Hisabi Settings`
  - `Hisabi Custom Currency`
  - `Hisabi FX Rate`
  - (existing) `Hisabi Account`, `Hisabi Category`, `Hisabi Transaction`
- Added allowlists, create-required field validation, field aliases, datetime normalization, and JSON field normalization (`enabled_currencies`) in `api/v1/sync.py`.
- Kept wallet authority server-side (`payload.wallet_id` mismatch rejected, server injects request wallet for wallet-scoped doctypes).
- Added int32 clamping of `client_created_ms` / `client_modified_ms` in `utils/sync_common.py` to prevent silent rejection from DB int truncation.
- Hardened test harness compatibility for `sync_push` Response payloads in `tests/test_sync.py`.
- Added backend verifier script: `hisabi_backend/hisabi_backend/scripts/verify_sync_e2e.sh`.

## Gates and verifiers

### Backend gates (run)
- `python3 -m py_compile hisabi_backend/hisabi_backend/api/v1/auth.py hisabi_backend/hisabi_backend/tests/test_sync.py`
- `python3 -m py_compile hisabi_backend/hisabi_backend/api/v1/sync.py hisabi_backend/utils/sync_common.py hisabi_backend/hisabi_backend/tests/test_sync.py`
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync` (PASS: 22 tests)
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync --test test_sync_push_persists_settings_currency_fx_accounts_categories_transactions` (PASS)
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync --test test_sync_pull_enforces_wallet_scope_for_fx_and_transactions` (PASS)

### Backend verifier
- `bash hisabi_backend/hisabi_backend/scripts/verify_sync_e2e.sh <site>`
  - Runs the two wallet-scope + multi-currency/FX transaction e2e tests above.

## Scenarios proven

- Device registration/link never creates `Hisabi Device` without `wallet_id`.
- Wallet-scoped sync push persists:
  - settings (base/default currency + enabled currencies),
  - custom currency,
  - FX rates,
  - accounts/categories,
  - transactions (including FX-related fields).
- `doc_version` increments on accepted creates/updates.
- Pull is wallet-scoped: data from another wallet is excluded.
- Transaction references remain wallet-consistent after pull (`account`, `category` remain valid).
- Seed warning behavior remains covered in `test_sync.py` (`seed_records_empty` path intact).

## Known limitations

- Frontend workspace for requested RC gates (`src/dev/test_sync_wallet_id_guardrails.ts`, `npm run test:hydration`, `npm run test:reachability`, new frontend verifier wiring) is not present in this environment, so frontend Phase B/C work could not be executed here.
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_auth_v2` currently fails due pre-existing auth_v2 test harness/environment issues unrelated to Phase A/B touched files; sync/auth-v1 gates above are passing.
