# Sprint 13 Log (Backend) - Sessionless Token Auth

Date: 2026-02-11

## Delivered

- Auth v2 login/register now enforce sessionless, token-only flow.
- Explicitly mark auth requests as JSON and avoid session/cookie emission on auth endpoints.
- Login no longer invokes Frappe session creation (`post_login`) for token auth.

## Tests

- Added `hisabi_backend/tests/test_auth_sessionless.py` to assert login/register succeed without cookies/CSRF.

## Gate Scripts

- Updated `hisabi_backend/scripts/verify_auth_smoke.sh` to force no-cookie curl calls.

## Evidence

- `bench --site hisabi.yemenfrappe.com migrate`: NOT RUN (local verification pending).
- `verify_auth_smoke.sh`: NOT RUN (local verification pending).
