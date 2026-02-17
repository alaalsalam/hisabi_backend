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

## Multi-currency rollout (2026-02-17)
### Backend scope
- Added account multi-currency fields and sync contract support:
  - `is_multi_currency`
  - `base_currency`
  - `group_id`
  - `parent_account`
- Added parent/child account routing for transaction sync and pull payload enrichment with:
  - `supported_currencies`
  - `sub_balances`
  - `total_balance_base`
- Added base-currency mutation guard: base currency change is rejected unless balances are zero.
- Added tests in `hisabi_backend/hisabi_backend/tests/test_accounts.py`.

### Frontend scope
- Added account creation mode toggle: single currency vs multi-currency.
- Multi-currency account creation now enqueues parent + base child accounts.
- Transaction flow now supports routing to currency child accounts with FX hints/tooltips.
- Accounts page now groups multi-currency balances and exposes expandable sub-balances.
- Help page updated with multi-currency usage explanation.

### Verification runbook
- Dependency fix:
  - `npm install -D vite-plugin-pwa`
- Migration:
  - `bench --site hisabi.yemenfrappe.com migrate`
- Frontend build gates:
  - `npm run build:dev`
  - `npm run build` (required for `test:reachability` manifest gate)
- Frontend test gates:
  - `npm run test:hydration`
  - `npm run test:reachability`
  - `npm run test:sync-multicurrency-fx-e2e`
- Backend targeted gate:
  - `bench --site hisabi.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_accounts`

### Environment note
- Full backend suite command `bench --site hisabi.yemenfrappe.com run-tests --app hisabi_backend` currently fails in this environment due unrelated pre-existing setup issues (`Parent Department: All Departments` and invalid encryption key format), not caused by the multi-currency changes.
