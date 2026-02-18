# Hisabi Backend Deployment Runbook (RC1)

Purpose: operator runbook for `hisabi.yemenfrappe.com` deploy and post-deploy verification.
When to use: during release windows and incident-driven redeploys.
Safety: commands below are operational only (migrate/restart/cache clear/HTTP checks), not data-debug mutations.

## Apply Code Changes Safely
1. Update code on server (git pull or deploy pipeline).
1. Clear caches (safe, non-destructive):
   1. `bench --site hisabi.yemenfrappe.com clear-cache`
1. Restart services to load new Python code:
   1. `sudo /usr/local/bin/bench restart`
1. Confirm processes updated (pick one):
   1. `bench doctor` (if available)
   1. `bench --site hisabi.yemenfrappe.com status` (if available)
1. Validate auth endpoints:
   1. `BASE_URL=https://hisabi.yemenfrappe.com ORIGIN=http://localhost:8082 bash /home/frappe/frappe-bench/apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_auth_smoke.sh | tee /tmp/auth_smoke.out`

Notes:
- Python code changes do not load until Frappe/worker processes restart.
- If you changed DocType JSON or patches, run:
  - `bench --site hisabi.yemenfrappe.com migrate`
  - Then `sudo /usr/local/bin/bench restart`

## Sudoers Hardening (frappe user)

### Target State (minimal NOPASSWD)
Create a dedicated sudoers include file:

```conf
# /etc/sudoers.d/frappe-hisabi
Defaults:frappe !requiretty
Cmnd_Alias HISABI_BENCH = /usr/local/bin/bench restart, /usr/local/bin/bench --version
Cmnd_Alias HISABI_NGINX = /usr/sbin/nginx -t, /bin/systemctl reload nginx
frappe ALL=(root) NOPASSWD: HISABI_BENCH, HISABI_NGINX
```

### Safe Apply Steps
1. Create/edit the file using visudo:
   1. `sudo visudo -f /etc/sudoers.d/frappe-hisabi`
1. Remove any unrestricted sudo entries:
   1. Search for `frappe ALL=(ALL : ALL) ALL` in `/etc/sudoers` and `/etc/sudoers.d/*`.
   1. Remove those lines using `visudo` (never edit sudoers with a raw text editor).
1. Validate sudoers syntax:
   1. `sudo visudo -cf /etc/sudoers`
   1. `sudo visudo -cf /etc/sudoers.d/frappe-hisabi`
1. Ensure permissions:
   1. `sudo chmod 0440 /etc/sudoers.d/frappe-hisabi`
1. Verify effective permissions:
   1. `sudo -l -U frappe`
   1. Confirm there is no `(ALL : ALL) ALL` entry.

### CI-like Check (Manual)
Run these before each deploy window:
1. `sudo /usr/local/bin/bench --version`
1. `sudo /usr/sbin/nginx -t`
1. `sudo /bin/systemctl reload nginx`
1. `sudo /usr/local/bin/bench restart`

## Release Verification Scripts (Official)
Run from repo root (`/home/frappe/frappe-bench/apps/hisabi_backend`):

```bash
BASE_URL=https://hisabi.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/hisabi_backend/scripts/verify_auth_smoke.sh
```

```bash
BASE_URL=https://hisabi.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/hisabi_backend/scripts/verify_sync_pull.sh
```

```bash
BASE_URL=https://hisabi.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/hisabi_backend/scripts/verify_sync_push_e2e.sh
```

```bash
BASE_URL=https://hisabi.yemenfrappe.com \
ORIGIN=http://localhost:8082 \
bash hisabi_backend/hisabi_backend/scripts/verify_sync_pull_pagination.sh
```

Requirement note: CORS must be configured only via Frappe `allow_cors` in `site_config.json`.

## Troubleshooting Checklist
1. Auth errors after deploy:
   1. Ensure `bench restart` was run.
   1. Confirm `encryption_key` exists in `site_config.json` (required for device token hashing).
1. Token errors (`token_revoked`, `token_expired`):
   1. Re-login to get a new device token.
   1. Check device status in `Hisabi Device` (blocked/revoked).
1. Sudo failures:
   1. Re-run `visudo -cf` checks.
   1. Verify the file permissions are `0440`.

## Sprint 06 Deploy + Verification Checklist
Run from `/home/frappe/frappe-bench`.

1. Deploy code to `main` for backend and frontend.
1. Run migration:
   1. `bench --site hisabi.yemenfrappe.com migrate`
1. Clear site cache:
   1. `bench --site hisabi.yemenfrappe.com clear-cache`
1. Restart services:
   1. `sudo /usr/local/bin/bench restart`
1. Run localhost gate suite:
   1. `BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_auth_smoke.sh`
   1. `BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_sync_push_e2e.sh`
   1. `BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_bucket_reports.sh`
   1. `BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_bucket_effectiveness.sh`
1. Run frontend contract verifier:
   1. `cd /home/frappe/frappe-bench/apps/hisabi_backend/hisabi-your-smart-wallet`
   1. `eval "$(BASE_URL=http://127.0.0.1:18000 bash ../hisabi_backend/hisabi_backend/scripts/mint_device_token.sh)" && HISABI_BASE_URL=http://127.0.0.1:18000 HISABI_TOKEN="$HISABI_TOKEN" node --import tsx src/dev/verifyReportsContract.ts`
1. Optional production diagnostics:
   1. `curl -sS https://hisabi.yemenfrappe.com/api/method/hisabi_backend.api.v1.health.diag`
