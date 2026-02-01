# Backend Current State (Hisabi Backend)

Evidence is derived from DocType JSONs, controllers, utils, API endpoints, and workspace fixtures in this repo.

## DocTypes (existing)
Source: `hisabi_backend/hisabi_backend/doctype/*/*.json`

### Wallet & auth-related
- **Hisabi Wallet**: `hisabi_backend/doctype/hisabi_wallet/hisabi_wallet.json` (client_id, wallet_name, status, owner_user, sync fields).
- **Hisabi Wallet Member**: `hisabi_backend/doctype/hisabi_wallet_member/hisabi_wallet_member.json` (wallet, user, role, status, joined_at/removed_at).
- **Hisabi Wallet Invite**: `hisabi_backend/doctype/hisabi_wallet_invite/hisabi_wallet_invite.json` (invite_code, token, target_phone/email, role_to_grant, status, expires_at).
- **Hisabi User**: `hisabi_backend/doctype/hisabi_user/hisabi_user.json` (user, default_wallet, phone_verified, locale, name_ar).
- **Hisabi Device**: `hisabi_backend/doctype/hisabi_device/hisabi_device.json` (device_id, platform, status, token hashes, timestamps).

### Core finance
- **Account**: `hisabi_backend/doctype/hisabi_account/hisabi_account.json` (account_name, account_type, currency, balances, archived, sort_order).
- **Category**: `hisabi_backend/doctype/hisabi_category/hisabi_category.json` (category_name, kind, parent_category, default_bucket).
- **Transaction**: `hisabi_backend/doctype/hisabi_transaction/hisabi_transaction.json` (transaction_type, date_time, amount, account, to_account, category, bucket, fx_rate_used, amount_base).
- **Budget**: `hisabi_backend/doctype/hisabi_budget/hisabi_budget.json` (budget_name, period, scope_type, category, currency, amount, amount_base, spent_amount, dates).
- **Goal**: `hisabi_backend/doctype/hisabi_goal/hisabi_goal.json` (goal_name, goal_type, currency, target_amount, current_amount, progress_percent, remaining_amount).
- **Debt**: `hisabi_backend/doctype/hisabi_debt/hisabi_debt.json` (debt_name, direction, currency, principal_amount, remaining_amount, counterparty_type/phone).
- **Debt Installment**: `hisabi_backend/doctype/hisabi_debt_installment/hisabi_debt_installment.json` (debt, due_date, amount, status, paid_at/paid_amount).
- **Debt Request**: `hisabi_backend/doctype/hisabi_debt_request/hisabi_debt_request.json` (from_phone, to_phone, debt_payload_json, status).
- **Jameya**: `hisabi_backend/doctype/hisabi_jameya/hisabi_jameya.json` (jameya_name, monthly_amount, total_members, my_turn, period, status).
- **Jameya Payment**: `hisabi_backend/doctype/hisabi_jameya_payment/hisabi_jameya_payment.json` (jameya, period_number, due_date, amount, status, is_my_turn).

### Buckets & allocation rules
- **Bucket**: `hisabi_backend/doctype/hisabi_bucket/hisabi_bucket.json` (bucket_name, archived, sort_order).
- **Allocation Rule**: `hisabi_backend/doctype/hisabi_allocation_rule/hisabi_allocation_rule.json` (rule_name, scope_type, scope_ref, is_default, active).
- **Allocation Rule Line**: `hisabi_backend/doctype/hisabi_allocation_rule_line/hisabi_allocation_rule_line.json` (rule, bucket, percent, sort_order).
- **Transaction Allocation**: `hisabi_backend/doctype/hisabi_transaction_allocation/hisabi_transaction_allocation.json` (transaction, bucket, percent, amount, currency, rule_used, is_manual_override).

### Settings & FX
- **Hisabi Settings**: `hisabi_backend/doctype/hisabi_settings/hisabi_settings.json` (user_name, base_currency, enabled_currencies, locale, week_start_day, use_arabic_numerals).
- **FX Rate**: `hisabi_backend/doctype/hisabi_fx_rate/hisabi_fx_rate.json` (base_currency, quote_currency, rate, effective_date, source, last_updated).
- **Custom Currency**: `hisabi_backend/doctype/hisabi_custom_currency/hisabi_custom_currency.json` (code, name_ar, name_en, symbol, decimals).

### Sync & audit
- **Hisabi Sync Op**: `hisabi_backend/doctype/hisabi_sync_op/hisabi_sync_op.json` (op_id, entity_type, status, result_json, server_modified).
- **Hisabi Audit Log**: `hisabi_backend/doctype/hisabi_audit_log/hisabi_audit_log.json` (event_type, device_id, ip, user_agent, payload_json).

### Notably referenced but missing
- **Hisabi Attachment** is referenced in `hisabi_backend/api/v1/sync.py` (DOCTYPE_LIST) and `hooks.py` (doc_events), but there is **no** `hisabi_attachment` DocType folder or JSON in this repo.

## DocType controllers & validations
Source: `hisabi_backend/doctype/*/*.py`
- **Wallet & wallet member**: `hisabi_backend/doctype/hisabi_wallet/hisabi_wallet.py` (name/client_id enforcement + common sync fields), `hisabi_backend/doctype/hisabi_wallet_member/hisabi_wallet_member.py` (joined_at/removed_at normalization).
- **Transaction**: `hisabi_backend/doctype/hisabi_transaction/hisabi_transaction.py` (transfer account != to_account; auto allocations on insert/update).
- **Budget**: `hisabi_backend/doctype/hisabi_budget/hisabi_budget.py` (amount > 0, scope_type/category requirements, date range validation, overlap check, currency validation).
- **Goal**: `hisabi_backend/doctype/hisabi_goal/hisabi_goal.py` (goal_type, linked_debt required for pay_debt, target_amount > 0, currency validation).
- **Debt**: `hisabi_backend/doctype/hisabi_debt/hisabi_debt.py` (principal_amount > 0, direction validation, remaining_amount logic, status defaults).
- **Debt installment**: `hisabi_backend/doctype/hisabi_debt_installment/hisabi_debt_installment.py` (amount > 0, debt ownership, total installments <= principal).
- **Debt request**: `hisabi_backend/doctype/hisabi_debt_request/hisabi_debt_request.py` (status defaults, accepted/declined validation).
- **Allocation rule line**: `hisabi_backend/doctype/hisabi_allocation_rule_line/hisabi_allocation_rule_line.py` (percent 1–100, duplicate bucket validation, total percent <= 100, rule/bucket ownership checks).
- **Jameya**: `hisabi_backend/doctype/hisabi_jameya/hisabi_jameya.py` (amount/member validations, schedule creation in after_insert).
- **Jameya payment**: `hisabi_backend/doctype/hisabi_jameya_payment/hisabi_jameya_payment.py` (amount > 0).

## API v1 endpoints (current)
Source: `hisabi_backend/api/v1/*.py`

### Auth
- `hisabi_backend/api/v1/auth_v2.py`: `register_user`, `login`, `me`, `logout`, `device_revoke` (device token auth).
- `hisabi_backend/api/v1/auth.py`: legacy session auth (login/register + device register/link).

### Wallet collaboration
- `hisabi_backend/api/v1/wallets.py`: `wallets_list`, `wallet_create`, `wallet_invite_create`, `wallet_invite_accept`, `wallet_member_remove`, `wallet_leave`.

### Sync
- `hisabi_backend/api/v1/sync.py`: `sync_push`, `sync_pull` with `DOCTYPE_LIST` and conflict handling.

### Reports
- `hisabi_backend/api/v1/reports_finance.py`: `report_summary`, `report_budgets`, `report_goals`, `report_debts`.
- `hisabi_backend/api/v1/reports.py`: `bucket_summary`, `bucket_rules`.

### Allocations
- `hisabi_backend/api/v1/allocations.py`: `set_manual_allocations`, `rebuild_income_allocations`.

### Debts network
- `hisabi_backend/api/v1/debts.py`: `create_network_request`, `accept_request`, `decline_request`.

### Jameya
- `hisabi_backend/api/v1/jameya.py`: `mark_payment_paid`, `rebuild_schedule`.

### Devices
- `hisabi_backend/api/v1/devices.py`: `devices_list`, `revoke_device`.

### Health
- `hisabi_backend/api/v1/health.py`: `ping`.

## Auth middleware + security
- Bearer auth for Hisabi v1 endpoints: `hisabi_backend/utils/bearer_auth.py` hooked in `hooks.py` (before_request) for `/api/method/hisabi_backend.api.v1.*` except auth endpoints.
- Device token verification + revocation: `hisabi_backend/utils/security.py` (require_device_token_auth, issue_device_token_for_device).
- Wallet ACL enforcement: `hisabi_backend/utils/wallet_acl.py` (require_wallet_member, get_wallets_for_user).
- Wallet-scoped DocType enforcement: `hisabi_backend/utils/wallet_doc_events.py` + `hooks.py` doc_events.

## Reports / computed fields (server)
- Allocation engine: `hisabi_backend/domain/allocation_engine.py` (auto allocations + manual overrides).
- Recalculation engine: `hisabi_backend/domain/recalc_engine.py` (account balances, budget spent, goal progress, debt remaining, jameya status).

## Permissions (DocType ACL)
- Each DocType JSON defines permissions (example: `hisabi_backend/doctype/hisabi_account/hisabi_account.json`):
  - `Hisabi User` role has create/read/write with `if_owner`.
  - `Hisabi Admin` read-only.
  - `System Manager` full CRUD/report.

## Workspace fixture
- `hisabi_backend/workspace/hisabi/hisabi.json` provides a “Hisabi” workspace with links to Wallet, Account, Transaction, Bucket, Allocation Rule, Budget, Goal, Debt, Debt Request, Jameya, Device, Settings.

## CORS
- CORS helper exists in `hisabi_backend/utils/cors.py` (allow list + headers), but no hook wired in `hooks.py` for `before_request`/`after_request` to apply it.

## Patches
- `patches.txt` lists:
  - `hisabi_backend.patches.v1_2.add_user_lockout_fields`
  - `hisabi_backend.patches.v1_0.backfill_default_wallets`
  - `hisabi_backend.patches.v1_3.remove_user_custom_fields`
- Patch modules in `patches/v1_0`, `patches/v1_1`, `patches/v1_2`, `patches/v1_3`.

