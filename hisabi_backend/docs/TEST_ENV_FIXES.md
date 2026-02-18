# Test Environment Fixes

## Why this exists

`bench --site hisabi.yemenfrappe.com run-tests --app hisabi_backend` can fail on this site due to pre-existing environment state:

1. Missing link target for `Parent Department: All Departments` while test records are being created.
2. Invalid `encryption_key` format for Fernet, which breaks password field encryption in test record insertion.

These are test harness blockers, not product behavior bugs.

## What was changed

Test-only bootstrap logic was added in `hisabi_backend/hisabi_backend/tests/bootstrap.py` and is executed from `before_tests` hook:

1. `ensure_all_departments()`
   - Runs only in test context (`frappe.flags.in_test` or `FRAPPE_ENV=test`).
   - Ensures a Department named `All Departments` exists when `Department` DocType is available.
   - Uses `ignore_permissions` and `ignore_mandatory`.
   - Does not call `frappe.db.commit()`.

2. `ensure_test_encryption_key()`
   - Runs only in test context.
   - Validates the key using the same Fernet constructor behavior used by Frappe (`Fernet(encode(key))`).
   - If invalid or missing, sets a valid key in memory only:
     - `frappe.local.conf.encryption_key = Fernet.generate_key().decode()`
   - Does not write to `site_config.json`.

## Safety boundaries

1. No migration hooks were changed.
2. No production config files are modified.
3. Bootstrap behavior is scoped to tests only.

## Verification commands

Reproduce blockers before fix:

```bash
bench --site hisabi.yemenfrappe.com --force --verbose run-tests --doctype "Hisabi Device" --failfast
```

Validate blockers are removed:

```bash
bench --site hisabi.yemenfrappe.com --force --verbose run-tests --doctype "Hisabi Device" --failfast
```

Smoke run for app suite (functional failures may still exist outside this scope):

```bash
bench --site hisabi.yemenfrappe.com run-tests --app hisabi_backend
```
