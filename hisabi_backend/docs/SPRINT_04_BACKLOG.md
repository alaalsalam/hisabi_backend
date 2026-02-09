# Sprint 04 Backlog (Backend)

## Goal
Income Allocation Buckets / Cost Centers with deterministic ledger impact and wallet-scoped reporting.

## Data Model Proposal
- `Hisabi Bucket` (cost center)
  - identity: `name/client_id`
  - fields: `wallet_id`, `bucket_name`, `status`, `archived`, `sort_order`, `is_deleted`, `deleted_at`, `doc_version`
- `Hisabi Allocation Rule`
  - fields: `wallet_id`, `scope_type` (`wallet` or `account`), `scope_ref_id`, `is_default`, `active`, `is_deleted`, `doc_version`
- `Hisabi Allocation Rule Line`
  - fields: `wallet_id`, `rule`, `bucket`, `percent`, `sort_order`, `is_deleted`, `doc_version`
- `Hisabi Transaction Allocation`
  - fields: `wallet_id`, `transaction`, `bucket`, `amount_base`, `percent`, `is_deleted`, `doc_version`

## API / Sync Contract
- Add sync entity handling for:
  - `Hisabi Bucket`
  - `Hisabi Allocation Rule`
  - `Hisabi Allocation Rule Line`
  - `Hisabi Transaction Allocation`
- Preserve invariants for all new entities:
  - idempotent `op_id`
  - strict `base_version` conflict detection
  - wallet-scoped query/update/delete paths
  - deterministic pull ordering/cursor monotonicity
  - soft delete propagation

## Transaction Linkage
- On income transaction create/update/delete:
  - derive applicable allocation rule deterministically
  - create/update/delete allocation rows accordingly
  - keep account and bucket totals deterministic under replay
- Guardrails:
  - reject active rules where percentage sum != 100
  - reject cross-wallet bucket/rule references

## Reports Impact
- Add wallet-scoped reports:
  - bucket breakdown
  - bucket cashflow
  - allocation effectiveness
- Required filters:
  - `wallet_id` (required)
  - `date_from/date_to` (optional)
  - `account_id`, `bucket_id`, `type` (optional)
- Response contract:
  - stable `message` envelope
  - `warnings` array for FX/contract caveats

## UX/Client Integration Contracts
- Provide pull payloads that allow frontend to render:
  - bucket balances
  - rule/line configuration
  - per-transaction allocation details
- Conflict payloads for new entities must match existing conflict center expectations.

## Acceptance Criteria
- Income transaction of `100` with active rule `33/33/34`:
  - allocations persisted as `33`, `33`, `34` (sum exact `100`)
  - bucket balances reflect allocation locally and after pull
  - replay of same `op_id` does not duplicate allocation rows
  - update/delete transaction correctly adjusts/reverses bucket balances
- Missing `wallet_id` on bucket reports returns `422 invalid_request`.

## Gates To Add/Extend
- Extend push/pull e2e to include bucket/rule/allocation lifecycle.
- Add `verify_bucket_allocation_e2e.sh`:
  - create rule + income tx
  - assert allocation rows + totals
  - assert replay idempotency
  - assert delete reversal
