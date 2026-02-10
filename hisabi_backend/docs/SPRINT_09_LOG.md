# Sprint 09 Log (Backend) - Data Safety + Portability

Date: 2026-02-10

## Delivered

- Added backup API module: `hisabi_backend/api/v1/backup.py`
  - `backup.export(wallet_id, format=hisabi_json_v1)`
  - `backup.validate_restore(wallet_id, payload)`
  - `backup.apply_restore(wallet_id, payload, mode=merge)`
- Added v1 wrappers in `hisabi_backend/api/v1/__init__.py`
  - `backup_export`
  - `backup_validate_restore`
  - `backup_apply_restore`
- Export payload contract includes:
  - `meta`: format, exported_at, app_version, commit, wallet_id
  - `entities`: wallet-scoped sync entities (including tombstones)
- Restore validation checks:
  - wallet mismatch
  - missing required fields
  - duplicate id collision across wallets
  - invalid references for critical links
  - non-blocking warnings for unknown entities/shape
- Restore apply behavior:
  - merge/upsert by canonical id (name/client_id)
  - wallet ownership enforced on write
  - soft-delete fields preserved
  - idempotent on repeated apply

## Tests

- Added `hisabi_backend/tests/test_backup_export_contract.py`
- Added `hisabi_backend/tests/test_backup_restore_validate.py`
- Added `hisabi_backend/tests/test_backup_restore_idempotent.py`

## Gate Scripts

- Added `hisabi_backend/scripts/verify_backup_restore.sh`
- Updated `hisabi_backend/scripts/verify_local_gate_suite.sh` to include `verify_backup_restore.sh`

## Notes

- Backup format is `hisabi_json_v1`.
- No device tokens, passwords, or encryption secrets are exported.
- Restore apply runs validation first and returns `422` on critical validation errors.
