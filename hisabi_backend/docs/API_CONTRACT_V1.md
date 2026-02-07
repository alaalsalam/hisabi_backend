# Hisabi Backend API Contract v1

This document covers v1 endpoints used by the current frontend for **sync + reports**.

## Common Rules
- Base path style: `/api/method/<python.method.path>`.
- Auth header (required): `Authorization: Bearer <device_token>`.
- Content type:
  - `sync_push`/`sync_pull`: `application/json` (also tolerates form/query parsing).
  - reports: query params on GET.
- Wallet scope is mandatory for all sync/report reads and writes.

## Sync Endpoints

### 1) Sync Push
- Path: `/api/method/hisabi_backend.api.v1.sync.sync_push`
- Method: `POST`
- Required headers: `Authorization`, `Content-Type: application/json`
- Required body:
```json
{
  "device_id": "dev-123",
  "wallet_id": "wallet-u-abc123",
  "items": [
    {
      "op_id": "wallet-u-abc123:Hisabi Account:acc-1:create:1730000000000",
      "entity_type": "Hisabi Account",
      "entity_id": "acc-1",
      "operation": "create",
      "payload": {"client_id": "acc-1", "wallet_id": "wallet-u-abc123", "account_name": "Cash", "account_type": "cash", "currency": "SAR"},
      "base_version": 0
    }
  ]
}
```
- Response shape (`message` payload):
```json
{
  "message": {
    "results": [
      {
        "status": "accepted|duplicate|conflict|error",
        "entity_type": "Hisabi Account",
        "client_id": "acc-1",
        "doc_version": 3,
        "server_modified": "2026-02-07T10:00:00"
      }
    ],
    "server_time": "2026-02-07T10:00:00"
  }
}
```
- Top-level validation failures (HTTP 417):
  - `device_id is required`
  - `wallet_id is required`
  - `items is required`
  - `items must be a list`
  - unsupported doctype/type failures
- Per-item error codes (`results[i].error_code`):
  - `entity_type_required`, `unsupported_entity_type`, `invalid_operation`, `entity_id_required`,
  - `payload_must_be_object`, `wallet_id_mismatch`, `entity_id_mismatch`, `invalid_client_id`,
  - `base_version_required`, `base_version_invalid`, `missing_required_fields`, `invalid_field_type`,
  - `not_found`, `payload_too_large`, `wallet_id_must_equal_client_id`.

### 2) Sync Pull
- Path: `/api/method/hisabi_backend.api.v1.sync.sync_pull`
- Method: `POST`
- Required headers: `Authorization`, `Content-Type: application/json`
- Required body:
```json
{
  "device_id": "dev-123",
  "wallet_id": "wallet-u-abc123",
  "cursor": "2026-02-07T10:00:00",
  "limit": 500
}
```
- Response shape (`message` payload):
```json
{
  "message": {
    "items": [
      {
        "entity_type": "Hisabi Transaction",
        "entity_id": "tx-1",
        "client_id": "tx-1",
        "doc_version": 8,
        "server_modified": "2026-02-07T10:05:00",
        "payload": {"...": "..."},
        "is_deleted": 0,
        "deleted_at": null
      }
    ],
    "next_cursor": "2026-02-07T10:05:00",
    "server_time": "2026-02-07T10:05:00"
  }
}
```
- Errors:
  - HTTP 417 with `device_id is required`, `wallet_id is required`, or `invalid_cursor`.

## Reports Endpoints

### 3) Finance Summary
- Path: `/api/method/hisabi_backend.api.v1.reports_finance.report_summary`
- Method: `GET`
- Required headers: `Authorization`
- Required query params: `wallet_id`
- Optional query params: `from_date`, `to_date`, `currency`
- Behavior:
  - Date params normalized via `get_datetime`.
  - Totals computed from `COALESCE(amount_base, amount)`.
- Response shape (`message` payload):
```json
{
  "message": {
    "accounts": [],
    "totals": {"income": 0, "expense": 0, "net": 0, "total_income": 0, "total_expense": 0},
    "budgets": [],
    "goals": [],
    "debts": {"owed_by_me": 0, "owed_to_me": 0, "net": 0},
    "jameya_upcoming": [],
    "server_time": "2026-02-07T10:00:00"
  }
}
```

### 4) Finance Budgets
- Path: `/api/method/hisabi_backend.api.v1.reports_finance.report_budgets`
- Method: `GET`
- Required headers: `Authorization`
- Required query params: `wallet_id`
- Optional query params: `from_date`, `to_date`
- Response shape: `{ "message": { "budgets": [...], "server_time": "..." } }`

### 5) Finance Goals
- Path: `/api/method/hisabi_backend.api.v1.reports_finance.report_goals`
- Method: `GET`
- Required headers: `Authorization`
- Required query params: `wallet_id`
- Response shape: `{ "message": { "goals": [...], "server_time": "..." } }`

### 6) Finance Debts
- Path: `/api/method/hisabi_backend.api.v1.reports_finance.report_debts`
- Method: `GET`
- Required headers: `Authorization`
- Required query params: `wallet_id`
- Response shape: `{ "message": { "debts": [...], "totals": {...}, "server_time": "..." } }`

### 7) Bucket Summary
- Path: `/api/method/hisabi_backend.api.v1.reports.bucket_summary`
- Method: `GET`
- Required headers: `Authorization`
- Required query params: `wallet_id`
- Optional query params: `from_date`, `to_date`, `currency`
- Response shape: `{ "message": { "buckets": [...], "server_time": "..." } }`

### 8) Bucket Rules
- Path: `/api/method/hisabi_backend.api.v1.reports.bucket_rules`
- Method: `GET`
- Required headers: `Authorization`
- Required query params: `wallet_id`
- Response shape: `{ "message": { "rules": [...] } }`

## What is NOT Supported in v1 Contract
- No collaboration/family/group API contract in v1 scope.
- No invitations/acceptance flows in v1 contract.
- No remote transfer approval/rejection workflows in v1 contract.
- No split-bills/subscriptions/community API contract in v1 scope.
