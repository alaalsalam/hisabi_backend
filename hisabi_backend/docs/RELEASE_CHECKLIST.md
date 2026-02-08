# Backend Release Checklist (RC1)

Purpose: fast release validation checklist for `expense.yemenfrappe.com`.
When to use: immediately before and after backend deploy.
Safety: run only documented verification scripts and read-only checks; avoid ad-hoc debug writes.

## 1) Deploy Steps (bench/site)
- Pull/deploy backend code.
- Run migrations when schema/doctype/patches changed:
  - `bench --site <site-name> migrate`
- Clear cache:
  - `bench --site <site-name> clear-cache`
- Restart services/workers:
  - `sudo /usr/local/bin/bench restart`

Reference: `../DEPLOYMENT_RUNBOOK.md` (repo root runbook).

## 2) Official Verification Scripts
Run from backend app repo root (`/home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend`):

- Auth smoke:
```bash
BASE_URL=https://expense.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/scripts/verify_auth_smoke.sh
```

- Sync pull:
```bash
BASE_URL=https://expense.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/scripts/verify_sync_pull.sh
```

- Sync push e2e:
```bash
BASE_URL=https://expense.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/scripts/verify_sync_push_e2e.sh
```

- Sync pull pagination:
```bash
BASE_URL=https://expense.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/scripts/verify_sync_pull_pagination.sh
```

## 3) Sync Smoke Checklist
- Register/login returns device token.
- `sync_push` accepts valid payloads and rejects invalid ones with deterministic error status.
- Duplicate `op_id` returns deduped result (no duplicate write).
- `sync_pull` returns deltas with `next_cursor`.
- Soft-deleted records return `is_deleted/deleted_at` in pull feed.

## 4) CORS/Auth Header Verification
- Confirm one CORS source only (`allow_cors` in site config; app CORS middleware remains no-op).
- Confirm Bearer auth is enforced for v1 endpoints via:
  - `hooks.py` before-request hook (`hisabi_backend.utils.bearer_auth.authenticate_request`)
  - endpoint guards (`require_device_token_auth` / `require_device_auth`).
- Verify token lifecycle:
  - revoked device returns auth failure.
  - valid active token can access `me`, reports, and sync endpoints.

## 5) Post-Deploy Observability
- Check server logs for sync validation/conflict spikes.
- Spot-check `Hisabi Sync Op` rows for accepted/conflict/error distribution.
- Confirm device timestamps (`last_sync_at`, `last_pull_at`) update after smoke runs.
