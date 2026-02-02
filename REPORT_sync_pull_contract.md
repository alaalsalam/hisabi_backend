# Sync Pull Contract (WP3.3)

Scope: Document canonical `sync_pull` response contract and frontend consumption, based on current backend implementation and sync engine logic.

## Backend contract (sync_pull)
- Endpoint: `hisabi_backend.api.v1.sync.sync_pull` accepts `device_id`, `wallet_id`, optional `cursor` or `since`, optional `limit`. Evidence: signature and request parsing. (`hisabi_backend/api/v1/sync.py:991-1052`)
- Request-level validation returns HTTP 417 with top-level `{"error": "..."}` for missing `device_id`, `wallet_id`, or invalid cursor. Evidence: error response builder. (`hisabi_backend/api/v1/sync.py:1000-1069`)
- Success response is HTTP 200 with `{ "message": { "items": [...], "next_cursor": "...", "server_time": "..." } }`. Evidence: response builder and return. (`hisabi_backend/api/v1/sync.py:1138-1140`)
- Each item includes `entity_type`, `entity_id`, `client_id`, `doc_version`, `server_modified`, `payload`, plus `is_deleted`/`deleted_at` flags when deleted. Evidence: item construction. (`hisabi_backend/api/v1/sync.py:1109-1121`)
- Items are filtered by `wallet_id` (or wallet member/user ownership) and by `server_modified > cursor`. Evidence: filters and cursor usage. (`hisabi_backend/api/v1/sync.py:1084-1097`)

## Frontend consumption (sync engine)
- Frontend accepts new `items[]` contract and remains backward-compatible with legacy `changes` map by normalizing to items. Evidence: `rawItems` derivation. (`hisabi-your-smart-wallet/src/lib/syncEngine.ts:449-462`)
- Each item is normalized to include `entityType`, `clientId`, `docVersion`, `serverModified`, `isDeleted`/`deletedAt`, and payload. Evidence: `normalizedItems` mapping. (`hisabi-your-smart-wallet/src/lib/syncEngine.ts:464-498`)
- Items are applied in deterministic order and either deleted locally when `isDeleted`/`deletedAt` are present or upserted otherwise. Evidence: ordered loop + delete/upsert logic. (`hisabi-your-smart-wallet/src/lib/syncEngine.ts:500-598`)
- Conflict items from push are stored in `syncConflicts` for ConflictCenter usage. Evidence: conflict branch writes to `db.syncConflicts`. (`hisabi-your-smart-wallet/src/lib/syncEngine.ts:331-350`)

## Verification script (LIVE)
- Added `verify_sync_pull.sh` to exercise pull delta and delete flags. (`hisabi_backend/hisabi_backend/scripts/verify_sync_pull.sh:1-170`)
