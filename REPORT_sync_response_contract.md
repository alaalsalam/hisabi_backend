# Sync Push Response Contract (WP3.2)

Scope: Document the canonical `sync_push` response contract and how the frontend consumes it, based on current backend implementation and frontend sync engine usage.

## Request-level errors (HTTP 4xx)
- Missing required args or invalid request shape returns HTTP 417 with a top-level `{"error": "..."}` payload (e.g., `device_id`, `wallet_id`, `items`, items must be list). Evidence: `sync_push` request validation + early returns. (`hisabi_backend/api/v1/sync.py:660-680`) 
- Unsupported entity types / missing DocType are rejected at request level (HTTP 417) before per-item processing. Evidence: pre-scan and early return. (`hisabi_backend/api/v1/sync.py:696-699`, `hisabi_backend/api/v1/sync.py:713-718`)

## Success response (HTTP 200)
- `sync_push` returns `{ "message": { "results": [...], "server_time": "..." } }` on success. Evidence: response builder at return. (`hisabi_backend/api/v1/sync.py:984-987`)

## Per-item results (within `message.results`)
### Accepted / Duplicate
- Accepted item payload includes `status`, `entity_type`, `client_id`, `doc_version`, `server_modified`. Evidence: result construction for accepted items. (`hisabi_backend/api/v1/sync.py:930-936`)
- Duplicate create returns the same accepted shape (no error), using the existing record’s version/timestamp. Evidence: duplicate handling. (`hisabi_backend/api/v1/sync.py:745-752`)

### Conflict
- Conflict result uses `status="conflict"` and includes `entity_type`, `client_id`, `doc_version`, `server_modified`, and a minimal `server_record`. Evidence: conflict response builder and usage. (`hisabi_backend/api/v1/sync.py:541-549`, `hisabi_backend/api/v1/sync.py:803-806`)

### Per-item error
- Per-item errors use `status="error"` with `error_code` + `error_message` (and `error` preserved for backwards compatibility), plus optional `detail`. Evidence: `_build_item_error`. (`hisabi_backend/api/v1/sync.py:358-397`)
- Validation errors, not-found, payload-too-large, and wallet-creation constraints feed into this per-item error path. Evidence: validation + error usage. (`hisabi_backend/api/v1/sync.py:400-418`, `hisabi_backend/api/v1/sync.py:775-835`, `hisabi_backend/api/v1/sync.py:845-854`)

## Frontend consumption (sync engine)
- Frontend expects `results[]` in order, and treats `status` values (`accepted`, `duplicate`, `conflict`, others) to update the queue and local state. Evidence: sync engine result processing. (`hisabi-your-smart-wallet/src/lib/syncEngine.ts:304-385`)
- For per-item errors, frontend reads `error_code`/`error_message` (with fallback to legacy `error`), stores them in sync queue metadata, and marks items `rejected` (no retry loop). Evidence: per-item error handling. (`hisabi-your-smart-wallet/src/lib/syncEngine.ts:358-381`, `hisabi-your-smart-wallet/src/lib/db.ts:388-401`)

## Conflict UI inventory
- Conflict Center exists as a screen and uses `syncConflicts` storage for conflict handling; no separate rejection UI exists in inventory. Evidence: inventory entry. (`hisabi-your-smart-wallet/REPORT_frontend_inventory.md:74-77`)
