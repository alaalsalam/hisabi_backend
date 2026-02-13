# SYNC RC1 QA

## What was fixed
- Added backend endpoint alias `hisabi_backend.api.v1.list_wallets` returning `wallet_ids` + `default_wallet_id` from authoritative wallet memberships.
- Added backend tests:
  - `test_list_wallets_returns_existing_wallets` in `hisabi_backend/tests/test_auth.py`.
  - `test_sync_pull_full_load_without_cursor_returns_wallet_entities` in `hisabi_backend/tests/test_sync.py`.

## How to run backend gates
- `python3 -m py_compile hisabi_backend/api/v1/__init__.py hisabi_backend/api/v1/wallets.py hisabi_backend/tests/test_auth.py hisabi_backend/tests/test_sync.py`
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_sync`
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_auth --test test_list_wallets_returns_existing_wallets`

## Scenarios proven
- Existing user wallet memberships are discoverable via `list_wallets` and include both default + additional wallets.
- `sync_pull` with `cursor=None` returns full wallet-scoped entities (account/category/transaction) for initial device hydration.

## Known limitations
- `bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.tests.test_auth_v2` currently fails in this environment due pre-existing auth_v2 test/setup issues not introduced by this change set.
