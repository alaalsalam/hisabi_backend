# SPRINT 13 HOTFIX: Expect header tolerance for login/me

## Root cause summary
- Upstream/proxy paths may include `Expect` semantics; auth entrypoints needed to tolerate this and return explicit auth/validation status instead of bubbling into unexpected 417 behavior.

## Changes applied
- Added header hardening helper: `hisabi_backend/utils/request_headers.py` (`strip_expect_header`).
- Applied hardening in API entrypoints:
  - `hisabi_backend/hisabi_backend/api/v1/__init__.py`
  - `hisabi_backend/hisabi_backend/api/v1/auth_v2.py`
- Added explicit error status mapping for validation/auth/permission failures.
- Added regression tests:
  - `hisabi_backend/hisabi_backend/tests/test_auth_expect_header.py`
  - Asserts login/me with `Expect: 100-continue` never return 417.

## Commands executed
```bash
bench --site expense.yemenfrappe.com migrate
bench --site expense.yemenfrappe.com serve --port 18000
BASE_URL=http://127.0.0.1:18000 ORIGIN=http://localhost:8082 bash apps/hisabi_backend/hisabi_backend/hisabi_backend/scripts/verify_local_gate_suite.sh
bench --site expense.yemenfrappe.com run-tests --app hisabi_backend --module hisabi_backend.tests.test_auth_expect_header
```

## PASS evidence snippets
```text
Migrating expense.yemenfrappe.com
...
Executing hisabi_backend.patches.v1_0.fix_customizations_in_dashboards ...
```

```text
LOCAL GATE SUITE PASS
```

```text
---------------------------------------------------------------------
Ran 3 tests in ...

OK
```

## Before / after behavior
- Before: some auth paths could surface proxy/header edge-cases as 417 failures.
- After: login/me tolerate `Expect` input and respond with explicit 2xx/4xx JSON contracts, not 417.
