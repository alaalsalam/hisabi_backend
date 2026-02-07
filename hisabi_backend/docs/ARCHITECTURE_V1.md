# Hisabi Backend Architecture v1 (Option A)

## Backend Role in v1
- Backend serves as **sync replica + remote report provider**, not the runtime source of truth while client is offline.
- Frontend runtime authority remains Dexie; backend persists replicated wallet-scoped records through sync endpoints.
- Primary modules:
  - Sync: `hisabi_backend/api/v1/sync.py`
  - Reports: `hisabi_backend/api/v1/reports_finance.py`, `hisabi_backend/api/v1/reports.py`
  - Auth v2: `hisabi_backend/api/v1/auth_v2.py`, `utils/security.py`, `utils/bearer_auth.py`

## Auth v2 Device-Token Model
- Registration/login issue long-lived revocable device tokens (`auth_v2.register_user`, `auth_v2.login`).
- Tokens are prefixed `hisabi_` and stored hashed with server salt (`utils/security.py`: `hash_device_token_v2`).
- Protected v1 endpoints require `Authorization: Bearer <device_token>` via:
  - Global before-request hook (`hooks.py` -> `hisabi_backend.utils.bearer_auth.authenticate_request`).
  - Endpoint-level enforcement (`require_device_token_auth` or `require_device_auth`).
- Sync endpoints additionally require `device_id` request field that must match token device (`require_device_auth(device_id)`).

## Sync Contracts (Server Perspective)

### Push (`sync_push`)
- Endpoint: `POST /api/method/hisabi_backend.api.v1.sync.sync_push`
- Input: `device_id`, `wallet_id`, `items[]`.
- Item contract: `op_id`, `entity_type`, `entity_id`, `operation`, `payload`, optional `base_version`.
- Limits/guards:
  - Max items per push: `200`.
  - Payload size checks.
  - Wallet membership and role checks (`require_wallet_member`).
  - Entity allowlist + required fields + type validation.
- Output: per-item result with `accepted|duplicate|conflict|error`, plus `doc_version` and `server_modified` when accepted.

### Pull (`sync_pull`)
- Endpoint: `POST /api/method/hisabi_backend.api.v1.sync.sync_pull`
- Input: `device_id`, `wallet_id`, optional `cursor|since`, optional `limit` (capped at 500).
- Output: `items[]` delta feed + `next_cursor` + `server_time`.
- Delta basis: `server_modified > cursor` ordered ascending.

### Cursor + Soft Delete
- Cursor is timestamp-like (ISO or epoch-compatible parser in `sync_pull`).
- Soft delete fields are propagated in pull payload (`is_deleted`, `deleted_at`).
- Common sync metadata is normalized via `utils/sync_common.py` (`doc_version`, `server_modified`, delete markers).

## Reports Endpoints Used by Frontend
- Finance summary/budgets/goals/debts:
  - `hisabi_backend.api.v1.reports_finance.report_summary`
  - `hisabi_backend.api.v1.reports_finance.report_budgets`
  - `hisabi_backend.api.v1.reports_finance.report_goals`
  - `hisabi_backend.api.v1.reports_finance.report_debts`
- Bucket reports:
  - `hisabi_backend.api.v1.reports.bucket_summary`
  - `hisabi_backend.api.v1.reports.bucket_rules`

### Parameter/Behavior Notes
- `wallet_id` is required and validated (`validate_client_id` + `require_wallet_member`).
- Date filters use `get_datetime(...)` normalization.
- Summary totals use `COALESCE(amount_base, amount)` for transaction aggregation.
- Optional `currency` filters apply where implemented (not all endpoints use it consistently).

## Data Integrity Rules
- Idempotency/dedupe:
  - `op_id` persisted in `Hisabi Sync Op` with unique `(user, device_id, op_id)`; duplicates return stored result.
- Concurrency:
  - `base_version` mismatch produces conflict response (no silent overwrite).
- Validation:
  - `entity_id == payload.client_id` enforced.
  - `client_id` format enforced.
  - Link ownership/wallet consistency enforced.
  - Server-authoritative fields are stripped from client payload for protected doctypes.
- Recalculation:
  - On accepted mutations, backend recalculates balances/budgets/goals/debts/jameyas as needed.
