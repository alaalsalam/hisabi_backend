# Tests

## Baseline Checks
- `bench --site hisabi.yemenfrappe.com list-apps`
- `bench --site lovable.yemenfrappe.com execute solvfast_lovable.api.sessions.get_session --kwargs "{'name':'LS-00011'}"`
- `bench --site lovable.yemenfrappe.com execute solvfast_lovable.api.sessions.get_app_routes --kwargs "{'session':'LS-00011'}"`
- `curl -k -sSI https://hisabi.yemenfrappe.com/hisabi-wallet`
- `curl -k -sSI https://hisabi.yemenfrappe.com/assets/hisabi_backend/hisabi-your-smart-wallet/assets/<asset>.js`

## Frontend Checks
Run from `hisabi-your-smart-wallet` when frontend validation is requested:
- `npm run test:lovable-boot`
- `npm run build`

Run the checks/builds needed for the requested Hisabi work directly, including `npm run build` and `bench build --app hisabi_backend`. Do not read/print secrets, delete data, or force-push git unless the user names that action directly.

---

## Functional Test Scenarios

> Approved by the user on 2026-06-25. Derived from `business.md` §5/§9 and the deep-review roadmap
> (`project_memory/2026-06-25-deep-review-and-roadmap.md`). Implemented by `@tester` as Frappe
> `FrappeTestCase` API/unit tests. **Scenarios C3 and B3 are designed to CATCH known bugs (P0.1 / P0.1c)
> — if they fail, that is expected and valuable; do NOT change app logic to make them green.**

### Group A — Wallet RBAC & Isolation
- **TS-A1** — A `viewer` member cannot create/update a wallet-scoped record (e.g. Hisabi Transaction)
  → raises `PermissionError`, and a `permission_denied` event is written to Hisabi Audit Log.
- **TS-A2** — A `member` can create a transaction but cannot remove a member or delete the wallet
  (`wallet_member_remove` / `wallet_delete` → `PermissionError`).
- **TS-A3** — A non-member cannot read or write any record of a wallet they don't belong to
  (`sync_push` / `sync_pull` rejected).
- **TS-A4** — An `owner` / `admin` can create an invite and remove a member successfully.
- **TS-A5** — Cross-wallet link is rejected: a transaction whose `account` belongs to another wallet
  → `PermissionError` (`ensure_link_ownership`).

### Group B — FX / Multi-currency
- **TS-B1** — A transaction in a non-base currency stores a correct `amount_base` using the effective
  FX rate (`resolve_default_fx_rate` / `fx_rate_used`).
- **TS-B2** — `seed_wallet_default_fx_rates` does NOT overwrite a rate whose `source ∈ {custom, api}`;
  it only fills missing/default rates (unless `overwrite_defaults`).
- **TS-B3** — A bucket/report total aggregates correctly across mixed-currency transactions
  (`bucket_summary` / `report_cashflow_by_bucket`). *(May surface P0.1c — record actual vs expected.)*

### Group C — Bucket Allocation Integrity
- **TS-C1** — `set_manual_allocations` rejects allocations whose percents don't sum to 100% (±0.0001)
  or amounts don't equal the tx value (±0.01) → HTTP 422 `invalid_bucket_allocation`.
- **TS-C2** — Income-bucket allocation only attaches to income transactions; expense bucket assignment
  only to expense transactions; an archived/inactive bucket is rejected.
- **TS-C3** — *Integrity guard:* after auto-allocation then a manual override, the canonical
  (`Hisabi Transaction Bucket`) and legacy (`Hisabi Transaction Allocation`) tables agree on
  bucket→amount and on manual-override state. *(Designed to CATCH the P0.1 divergence.)*

### Group D — Core Transaction & Balance Flow
- **TS-D1** — Create account → post income then expense via the wallet-scoped path → account
  `current_balance` updates deterministically to the expected value.
- **TS-D2** — Wallet isolation: user A's `sync_pull` never returns records belonging to user B's wallet.

### Group E — Import Session (Plan 0001-B)
- **TS-E1** — `create_session` persists rows and returns `session_id`; idempotent on same `client_id`.
- **TS-E2** — `get_sessions` / `get_session` return correct data; cross-wallet read is denied.
- **TS-E3** — `confirm_session` marks selected rows as imported, sets status `confirmed`; calling again is idempotent.
- **TS-E4** — `cancel_session` sets status `cancelled`; cancelling a confirmed session raises; confirming a cancelled session raises.
- **TS-E5** — `create_session` to a wallet the caller is not a member of raises `PermissionError`.

### Group F — AI Advisor (Plan 0001-C/D)
- **TS-F1** — `status` returns `available=False` when AI disabled; `available=True` with a configured key; `available=False` when enabled but no key.
- **TS-F2** — `get_financial_context` returns required financial summary fields; cross-wallet request raises `PermissionError`.
- **TS-F3** — `parse_transactions` (AI enabled, mocked): happy path enriches raw text; non-JSON response returns `fallback`; empty text raises; AI disabled raises.
- **TS-F4** — `parse_bank_statement` (AI enabled, mocked): AI enriches rows; AI error falls back gracefully; empty rows return empty; AI disabled returns `fallback`.
- **TS-F5** — `apply_ai_action correction`: updates transaction category; missing `transaction_id` raises; cross-wallet raises `PermissionError`.
- **TS-F6** — `apply_ai_action bulk_recategorize`: updates all listed transactions to new category.
- **TS-F7** — `chat`: creates new session; resumes existing session; persists user+assistant messages; detects `ACTION::` block and strips it from response; empty message raises; AI disabled raises; cross-wallet raises.
- **TS-F8** — `get_chat_sessions` / `get_chat_session`: lists sessions; returns messages; cross-wallet denied; not-found raises.

### Group G — Allocation Consolidation (Plan 0001-A)
- **TS-G1** — After migration: `Hisabi Transaction Bucket` has the 4 new fields (`currency`, `amount_base`, `is_manual_override`, `rule_used`).
- **TS-G2** — Auto-allocation writes only to the canonical table (no dual-write to legacy `Hisabi Transaction Allocation`).
- **TS-G3** — New bucket rows default `is_manual_override=0`; `currency` is populated from the transaction.
- **TS-G4** — Reports SQL can read `currency`/`amount_base` from the canonical table.
- **TS-G5** — Zero-amount transactions do not raise during auto-allocation.

---

## Latest Test Run

**Date**: 2026-09-18 — **full testing-phase sweep (all 59 test modules incl. 3 doctype-scaffold
tests, run per-module)**

**Command** (per-module; same isolation rationale as before):
```
for m in <all test_*.py under hisabi_backend/tests/>; do
  bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.hisabi_backend.tests.$m
done
for m in <the 3 doctype/<name>/test_<name>.py scaffold files>; do
  bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.hisabi_backend.doctype.<name>.test_<name>
done
```

**Result: 59 / 59 modules GREEN (254 tests). 0 failures, 0 errors, 0 skips.**

Test count grew from 45→56 modules organically since the 2026-07-03 run (new coverage added for
backup/restore, recurring rules, reports-contract-v2, review/issues, realtime, health-diag, etc. —
no scenario changes were requested for this growth). This run also, for the first time, executed
the 3 auto-generated doctype-scaffold test files (`test_hisabi_debt`, `test_hisabi_doctype`,
`test_hisabi_device`) which the previous per-module command list did not include.

### Environment/infrastructure fixes applied (2026-09-18) — zero app-source edits, zero scenario changes

Before any of the 59 modules could run, **every single one failed** with the same
`frappe.exceptions.LinkValidationError: Could not find Parent Item Group: All Item Groups` (or a
deeper cascade). Root cause: unrelated to hisabi_backend logic — a **Frappe framework/runner change**
plus **one site-data mismatch**:

1. **Deprecated test base class.** All 59 test files imported `FrappeTestCase` from
   `frappe.tests.utils`, which in the currently installed Frappe version is a deprecated
   backward-compat shim. The test runner categorizes it as `"old-frappe-test-class-category"`,
   which eagerly walks and preloads test-record fixtures for **every** `test_*.py` file across the
   **entire app**, including doctypes that transitively depend on ERPNext master data. **Fix**:
   changed the import in all 59 files to `from frappe.tests import IntegrationTestCase as
   FrappeTestCase` — the modern, lazily-loading base class Frappe core itself now uses. Zero
   behavior change to any test body; every `class Test...(FrappeTestCase)` line is untouched.
2. **Site locale mismatch.** ERPNext's own test bootstrap (`erpnext/tests/utils.py`, imported
   transitively by the legacy category above) hardcodes the English root record name
   `"All Item Groups"`, but this Arabic-configured site had created it as `"كل مجموعات الأصناف"`.
   **Fix** (approved by user): `frappe.rename_doc("Item Group", "كل مجموعات الأصناف", "All Item
   Groups")` — a standard, link-safe Frappe rename.
3. **Missing `payments` app.** With (1) fixed, the scaffold `test_hisabi_device.py` (which Frappe's
   doctype-folder naming convention auto-infers as testing doctype `"Hisabi Device"`) still failed:
   its `IntegrationTestCase.setUpClass` auto-generates fixture records for `Hisabi Device`'s Link
   fields (`user`, `wallet_id`), which transitively reach `Company`/`Email Account` → `Payment
   Gateway` — a doctype provided by the separate `payments` app, **which is not installed on this
   bench at all** (no such app in `bench list-apps`, no such folder anywhere). This is a pre-existing
   environment gap, not a hisabi_backend defect. Since `register_device` (the function under test)
   already has thorough, passing coverage across 18 other test files (`test_auth`, `test_wallets`,
   `test_accounts`, `test_sync*`, etc.), the scaffold file was scoped rather than the environment
   patched further: added `IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Hisabi Wallet"]` (Frappe's
   own documented per-module override) to `test_hisabi_device.py`, since the test already creates
   its own `User` doc explicitly and never touches a real `Hisabi Wallet`.

**Note for later phases**: the missing `payments` app may resurface for *any* future doctype-folder
test file whose doctype links (directly or transitively) to `User`/`Company`. Installing the
`payments` app on this bench (or seeding a minimal `Payment Gateway` fixture) would remove the need
for this per-file workaround — flagged here for `@performance`/`@go-live` awareness, not actioned
since it's outside hisabi_backend and outside the testing phase's file-edit scope.

---

## Earlier Run (2026-07-03, historical)

**full backend-phase sweep (all 45 test modules, run per-module)**

**Command** (per-module; `--app` whole-run has cross-module isolation issues — shared Single
doctypes, rate-limit counters, `frappe.local.request` leakage — so modules are run individually):
```
for m in <all test_*.py>; do
  bench --site hisabi.yemenfrappe.com run-tests --module hisabi_backend.hisabi_backend.tests.$m
done
```

**Result: 45 / 45 modules GREEN (~194 tests).**
- **0 failures, 0 errors.**
- **1 skip** — `test_auth_v2.test_register_user_type_is_website` (documents an app-config finding, see below).
- **1 expected failure** — `test_bucket_cashflow_report.test_fx_missing_warning_excludes_rows_without_rate`
  (documents known bug **P0.1c / TS-B3**, see below). Kept visible via `@unittest.expectedFailure`.

### Backend-phase work applied (2026-07-03) — TEST CODE ONLY, zero app-source edits

Brought the whole suite green by fixing **stale tests** that predated the API's evolution — the v2
endpoints now return **structured error dicts / werkzeug `Response` (401/403/`INVALID_CREDENTIALS`)**
instead of raising, records are **autonamed** (look up by `client_id`, not hardcoded names), and sync
**rejects server-authoritative fields** (`remaining_amount`) and requires `currency` on updates.

| Module(s) | Stale-test root cause fixed (test-only) |
|---|---|
| `test_auth_v2`, `test_auth`, `test_auth_sessionless`, `test_security_rate_limits` | hex-letter phones (app correctly rejects) → digit-only; `assertRaises` → assert structured error code/`status_code`; complete `LoginManager` request fixture; real rate-limit (20/600s) + lockout contract |
| `test_wallets` | `assertRaises(PermissionError)` → assert `auth_failed`/`wallet_read_only` error dict + no write; re-auth as owner before `wallet_member_remove` (auth reads the Bearer token, not `device_id`) |
| `test_debts_installments`, `test_goals_recalc` | drop server-authoritative `remaining_amount` from payload; look up Debt/Goal by `client_id` (autonamed) |
| `test_allocation_engine` | add device auth to `setUp`; query canonical `Hisabi Transaction Bucket` (not legacy Allocation) |
| `test_jameya_schedule` | count payments by resolved autoname, not hardcoded `client_id` |
| `test_budgets_reports`, `test_transaction_balance_determinism` | pin wallet base currency in `setUp`; include `currency` on partial update |

### New regression tests added (2026-07-03) — `tests/test_ai_advisor_regression.py`

Guard three production bugs fixed this session that the existing (fully-mocked) AI tests could not catch:
- `_get_ai_settings` must **decrypt** the key via `get_password()` (not return the masked `****`).
- `_get_wallet_financial_context` must use real columns: Debt `counterparty_name` (not `party_name`),
  Transaction `note` (not `description`).

### App-source findings — RESOLVED 2026-07-03

1. **P0.1c mixed-currency FX (report accuracy) — FIXED.** `api/v1/reports_finance.py` `_resolve_fx_rate`
   now caches **None** (not the default rate) for a pair with no real FX rate, so every row of that
   pair is excluded consistently with an `fx_missing` warning. Test
   `test_bucket_cashflow_report.test_fx_missing_warning_excludes_rows_without_rate` converted from
   `@expectedFailure` → normal passing; other report modules verified unaffected.
2. **User type / desk access — FIXED.** `install.ensure_roles` now creates/repairs the `Hisabi User`
   role with `desk_access=0` (and added an `after_migrate` hook so existing sites are corrected), so
   registrants stay **Website Users**. Applied to the live DB. Test
   `test_auth_v2.test_register_user_type_is_website` un-skipped and passing.
3. **N+1 in `sync_pull` (perf) — FIXED 2026-07-04.** `sync.py` now batch-loads pull records with a
   single `frappe.get_all(fields=["*"])` per doctype for "batch-safe" doctypes (flat allow-listed
   projection with no child-table/virtual field, decided by `_pull_is_batch_safe`), and keeps the
   per-row `get_doc` only for child-table doctypes (e.g. Hisabi Bucket Template). Proven byte-identical
   to the old path (JSON parity over 105 records / 24 doctypes, 0 mismatch), all sync tests green, and
   ~10x faster record loading (300 rows: 0.091s → 0.009s).

### Earlier run (2026-07-01, historical)
50 tests across `test_ai_advisor` (28) + `test_import_session` (15) + `test_allocation_consolidated` (7),
all green. Fixes then: `category_type`→`kind`, AI chat controller class names, zero-amount allocation skip.
