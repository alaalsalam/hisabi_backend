# Backend Update Spec — Parity Now (Frontend ↔ Backend)

Scope: Implement only what the current frontend uses via sync, API calls, and core screens. Evidence and contracts are from:
- `hisabi-your-smart-wallet/REPORT_frontend_data_model.md`
- `hisabi-your-smart-wallet/REPORT_frontend_api_contract.md`
- `hisabi-your-smart-wallet/REPORT_frontend_localdb_sync.md`
- `hisabi_backend/REPORT_backend_current_state.md`
- `hisabi_backend/REPORT_gap_matrix.md`

## Must‑Fix Checklist (mapped to report evidence)

### Missing DocTypes
- **Hisabi Attachment**: frontend sync + receipt metadata requires it (`REPORT_frontend_data_model.md` Attachment; `REPORT_frontend_localdb_sync.md` sync tableMap includes `Hisabi Attachment`). Backend currently references it in `api/v1/sync.py` + `hooks.py` but DocType is missing (`REPORT_backend_current_state.md`, `REPORT_gap_matrix.md`).

### Field mismatches (sync payloads)
- **Account**: frontend uses `name` + `type` (`REPORT_frontend_data_model.md`) vs backend fields `account_name` + `account_type` (`REPORT_backend_current_state.md`).
- **Category**: frontend `name`, `parentId` vs backend `category_name`, `parent_category`.
- **Transaction**: frontend `accountId`, `toAccountId`, `categoryId`, `bucketId` → backend `account`, `to_account`, `category`, `bucket`. Backend `bucket` is Data (not Link) but validators expect Link (`utils/validators.py` in backend, implied by `REPORT_gap_matrix.md`).
- **Bucket**: frontend `name` vs backend `bucket_name`.
- **Allocation Rule / Line**: frontend `name`, `scopeRefId`, `ruleId`, `bucketId` vs backend `rule_name`, `scope_ref`, `rule`, `bucket`.
- **Transaction Allocation**: frontend `transactionId`, `bucketId`, `ruleIdUsed` vs backend `transaction`, `bucket`, `rule_used`.
- **Budget**: frontend only uses `amountBase` + no currency (`REPORT_frontend_data_model.md`) while backend requires `currency` + `amount` (`REPORT_backend_current_state.md`).
- **Goal**: frontend lacks `currency` + `target_amount`; backend requires these fields.
- **Debt Request**: frontend uses status `rejected`, backend uses `declined` (`REPORT_gap_matrix.md`).

### Report shape mismatches
- **report_summary**: frontend expects `totals.income/expense/net`, accounts include `account` key, but backend returns `total_income/total_expense` and `name` (`REPORT_gap_matrix.md`, `REPORT_frontend_api_contract.md`).
- **report_budgets**: frontend expects `budget` + `percent` keys; backend returns `name` + `spent_percent`.
- **report_goals**: frontend expects `goal` key; backend returns `name`.
- **report_debts**: frontend expects optional `totals` object (DebtReportTotals).
- **bucket_rules**: frontend expects `rule` key; backend returns `name` (`REPORT_gap_matrix.md`).

### Sync payload mismatches
- `sync_push` currently maps limited fields (FIELD_MAP) and does not map several frontend fields (account type, category parent, transaction account IDs, bucket IDs, etc.). This blocks correct sync for core entities (`REPORT_gap_matrix.md`).

### Auth/CORS requirements
- CORS helper exists but is not wired in `hooks.py` (`REPORT_backend_current_state.md`). Must ensure OPTIONS preflight returns 200 and no duplicate ACAO headers (requirement).

## Design: DocTypes & Fields (Parity Now)

### 1) Add DocType: **Hisabi Attachment** (required by sync + receipt upload)
Source requirements: `REPORT_frontend_data_model.md` Attachment + `REPORT_frontend_localdb_sync.md` (sync includes `Hisabi Attachment`).

Fields (snake_case to match `transformToFrappe` output):
- `user` (Link User, required)
- `wallet_id` (Link Hisabi Wallet, required)
- `client_id` (Data, required, unique)
- `owner_entity_type` (Data, required) — e.g., `Hisabi Transaction`
- `owner_client_id` (Data, required) — transaction client_id
- `file_id` (Data) — Frappe File docname from `/api/method/upload_file`
- `file_url` (Data)
- `file_name` (Data)
- `mime_type` (Data)
- `file_size` (Int)
- `sha256` (Data)
- common sync fields: `client_created_ms`, `client_modified_ms`, `doc_version`, `server_modified`, `is_deleted`, `deleted_at`

Indexes:
- Unique index on `client_id`.
- Composite index on `[wallet_id, owner_entity_type, owner_client_id]` for quick lookup.
- Index on `file_id` (optional) for debugging.

Controller validation (`hisabi_attachment.py`):
- Ensure `user` defaults to session user.
- If `owner_entity_type == 'Hisabi Transaction'`, verify transaction exists and `wallet_id` matches.

### 2) Update **Hisabi Transaction** bucket field type
- Change field `bucket` from **Data** → **Link (Hisabi Bucket)** to align with `validators.ensure_link_ownership` (backend) and frontend `bucketId` usage.
- No data loss expected since existing values are bucket IDs.

## API & Sync Alignment

### A) Sync field mapping (server-side)
Update `hisabi_backend/api/v1/sync.py` FIELD_MAP and pre-processing to map frontend field names → backend fields.

Required mappings (payload keys are snake_case from frontend `transformToFrappe`):
- **Hisabi Account**: `name` → `account_name`, `type` → `account_type`.
- **Hisabi Category**: `name` → `category_name`, `parent_id` → `parent_category`, `default_bucket_id` → `default_bucket`.
- **Hisabi Transaction**: `type` → `transaction_type`, `account_id` → `account`, `to_account_id` → `to_account`, `category_id` → `category`, `bucket_id` → `bucket`.
- **Hisabi Bucket**: `name` → `bucket_name`.
- **Hisabi Allocation Rule**: `name` → `rule_name`, `scope_ref_id` → `scope_ref`.
- **Hisabi Allocation Rule Line**: `rule_id` → `rule`, `bucket_id` → `bucket`.
- **Hisabi Transaction Allocation**: `transaction_id` → `transaction`, `bucket_id` → `bucket`, `rule_id_used` → `rule_used`.
- **Hisabi Budget**: `name` → `budget_name`, `category_id` → `category`.
- **Hisabi Goal**: `name` → `goal_name`, `type` → `goal_type`, `linked_account_id` → `linked_account`, `linked_debt_id` → `linked_debt`.
- **Hisabi Debt**: `name` → `debt_name`.
- **Hisabi Jameya**: `name` → `jameya_name`.

### B) Budget/Goal currency backfill (server-side)
Frontend models don’t supply `currency` or `amount` (`REPORT_frontend_data_model.md`). Backend requires them.

Rules (apply in sync_push or DocType validate):
- **Budget**: if `currency` missing, set to wallet base currency from `Hisabi Settings` for that wallet; if `amount` missing and `amount_base` present, set `amount = amount_base`.
- **Goal**: if `currency` missing, set to wallet base currency; if `target_amount` missing and `target_amount_base` present, set `target_amount = target_amount_base`.

### C) Debt Request status mapping
- Canonicalize to **`rejected`** (frontend uses `rejected`).
- Accept and map legacy `declined` to `rejected` during validation + patch existing rows.

## Reports: Response Shape Fixes (compat layer)

Update these endpoints to include frontend‑expected keys *without removing existing ones*:

1) `reports_finance.report_summary`
- `totals` should include `income`, `expense`, `net` plus keep `total_income`/`total_expense` for backwards compatibility.
- Account rows should include `account` key (copy from `name`) and `account_name`.

2) `reports_finance.report_budgets`
- Each row must include `budget` (copy from `name`) and `percent` (copy from `spent_percent` or computed).
- Include `remaining` as `amount - spent_amount`.

3) `reports_finance.report_goals`
- Each row must include `goal` (copy from `name`).

4) `reports_finance.report_debts`
- Add `totals`: `{ owed_by_me, owed_to_me, net }` computed from remaining_amount by direction.

5) `reports.bucket_rules`
- Each rule object must include `rule` (copy from `name`).

## Sync: Attachments
- Ensure `Hisabi Attachment` is part of DOCTYPE_LIST (already in `api/v1/sync.py`).
- Add mapping for attachment fields is not required if DocType uses snake_case fields listed above.

## Permissions / ACL
- `wallet_doc_events.validate_wallet_scope` already enforces wallet membership for wallet‑scoped doctypes. Ensure `Hisabi Attachment` is included (already listed). No changes to auth required.

## Migrations / Patches (idempotent)

1) **Patch: Debt Request status**
- Convert `declined` → `rejected` in `tabHisabi Debt Request`.
- Update DocType options + validation to accept `rejected` (and map legacy `declined`).

2) **Patch: Budget currency/amount backfill**
- For budgets where `currency` is NULL/empty, set from `Hisabi Settings.base_currency` for same wallet/user.
- If `amount` is NULL and `amount_base` is set, set `amount = amount_base`.

3) **Patch: Goal currency/target_amount backfill**
- For goals where `currency` is NULL/empty, set from `Hisabi Settings.base_currency`.
- If `target_amount` is NULL and `target_amount_base` is set, set `target_amount = target_amount_base`.

## Workspace update
- Add link to new DocType **Hisabi Attachment** in `hisabi_backend/workspace/hisabi/hisabi.json`.

## Verification Plan (commands)
- `bench --site hisabi.yemenfrappe.com migrate`
- `bench --site hisabi.yemenfrappe.com console`:
  - `frappe.get_meta('Hisabi Attachment')` should exist.
  - `frappe.db.get_value('Hisabi Attachment', {'client_id': 'att-test'}, 'name')` after insert.
- cURL tests:
  - OPTIONS preflight to `/api/method/hisabi_backend.api.v1.register_user`.
  - Register/login/me with Bearer token.
  - Reports endpoints return JSON with expected keys (budget/goal/account/totals).
  - Sync push transaction w/ bucket + account mapping; ensure no `invalid_account`.

## WP1 Decisions (schema vs derived fields)
- `Wallet.isDefault` is derived in API responses (compare wallet id to `default_wallet_id`); no DocType field added.
- `selectedWalletId` remains local-only in the client; no `Hisabi Settings` schema change.
