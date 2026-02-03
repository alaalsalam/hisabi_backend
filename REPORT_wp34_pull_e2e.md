# WP3.4 Pull E2E Report

Date: 2026-02-03
Scope: Frontend pull path validation + minimal verification additions (no backend contract changes).

## Findings
- Pull path consumes `message.items` (preferred) and falls back to `message.changes` mapping; items are normalized and applied to IndexedDB per DocType. Evidence: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:434-515` and `hisabi-your-smart-wallet/src/lib/syncEngine.ts:520-644`.
- Cursor persistence is per wallet via `syncState.walletId` and updated on `next_cursor`, with legacy localStorage migration for the old cursor key. Evidence: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:113-144`, `hisabi-your-smart-wallet/src/lib/syncEngine.ts:647-656`.
- Soft-delete handling now preserves rows for types that have deletion fields (wallets, transactions, buckets, allocation rules, attachments) by setting `isDeleted`/`deletedAt` instead of hard-deleting, while still hard-deleting types without deletion semantics. Evidence: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:69-75`, `hisabi-your-smart-wallet/src/lib/syncEngine.ts:537-579`.
- Verification script now asserts `is_deleted` + `deleted_at` on delete pulls and checks repeat cursor pulls return empty results. Evidence: `hisabi_backend/hisabi_backend/scripts/verify_sync_pull.sh:248-307`.

## Risks / Gaps
- Some synced entities do not define local soft-delete fields (e.g., Accounts, Categories). Deletion for those types will continue to hard-delete locally; if UI expects a soft delete marker, it may need follow-up. Evidence: `hisabi-your-smart-wallet/src/lib/db.ts:121-175` and `hisabi-your-smart-wallet/src/lib/syncEngine.ts:537-579`.
- `useCloudAuthStore.syncCursor` is global, while the authoritative cursor is stored per wallet in `syncState`. Switching wallets relies on `syncState` and does not read from the store; if UI or diagnostics read the store directly, cursor display may be stale. Evidence: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:113-144`, `hisabi-your-smart-wallet/src/stores/useCloudAuthStore.ts:19-66`.

## Code Touchpoints (Key Decisions)
- Pull normalization (`message.items` + `message.changes`) and ordered application: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:434-515`.
- Cursor persistence per wallet: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:113-144`, `hisabi-your-smart-wallet/src/lib/syncEngine.ts:647-656`.
- Soft delete handling: `hisabi-your-smart-wallet/src/lib/syncEngine.ts:69-75`, `hisabi-your-smart-wallet/src/lib/syncEngine.ts:537-579`.
- Verification script checks for delete flags + repeat cursor empty: `hisabi_backend/hisabi_backend/scripts/verify_sync_pull.sh:248-307`.

## Manual Smoke Checklist (No UI Changes)
1. Go offline.
2. Create a local item (e.g., Account or Transaction).
3. Reconnect, trigger sync (push), verify server accepts.
4. Trigger pull; verify the item appears in local DB and UI.
5. Delete the item on another device or via API.
6. Pull again; verify local data reflects deletion (soft delete where supported).

## Verification Commands + Results
- `bash /home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_sync_pull.sh`
  - Result: Not run here (network access restricted in this environment).

Notes:
- The script now asserts `is_deleted` and `deleted_at` in pull responses and confirms a repeat cursor pull yields no items.
