# Sprint 3 Finance Engine

## Scope
This sprint delivers server-authoritative financial recalculation and reporting for:
- Budgets
- Goals (saving + debt payoff)
- Debts + Installments + Debt Requests
- Jameya + payment schedules

Sync remains unchanged (DocType name matches entity_type, name == client_id, soft delete, idempotent ops).

## Core Rules
### Budgets
- `period`: weekly | monthly | quarterly | yearly | custom
- `scope_type`: total | category
- A budget is valid only within its `start_date`/`end_date` window.
- Budgets cannot overlap for the same user + scope + date range.
- `spent_amount` is server-derived from expense transactions matching scope and date range.

### Goals
- `goal_type`:
  - `save`: progress derived from linked account balance (if provided).
  - `pay_debt`: progress derived from linked debt remaining amount.
- `target_amount` must be > 0.
- `pay_debt` goals require `linked_debt`.
- Derived fields: `current_amount`, `remaining_amount`, `progress_percent`.

### Debts + Installments
- `principal_amount` > 0.
- `remaining_amount` is server-derived from paid installments.
- Installment amounts cannot exceed principal in aggregate.
- Debt status becomes `closed` when remaining amount is 0.

### Debt Requests
- Requests are created with `status=pending`.
- Accept/decline APIs update status.
- Accept can optionally create a debt using the request payload.

### Jameya
- Schedule is generated on create or rebuild.
- `monthly_amount * total_members = total_amount`.
- Payment schedule created using `period` and `start_date`.
- `is_my_turn` is set for the appropriate payment entry.

## Recalculation Engine
Module: `hisabi_backend/domain/recalc_engine.py`
- Recalculates derived fields after sync push.
- Ignores deleted transactions and documents.
- Functions:
  - `recalc_account_balance`
  - `recalc_budget_spent`
  - `recalc_goal_progress`
  - `recalc_debt_remaining`
  - `recalc_jameya_status`

Sync pushes trigger recalculation for affected entities in batches.

## Reports
Module: `hisabi_backend/api/v1/reports_finance.py`
- `report_summary`: accounts, totals, budgets, goals, debts, upcoming jameya payments
- `report_budgets`
- `report_goals`
- `report_debts`

All reports require user or device auth and are filtered by user ownership.
