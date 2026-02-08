"""Reporting APIs for buckets."""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe.utils import get_datetime, now_datetime

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


@frappe.whitelist(allow_guest=False)
def bucket_summary(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    currency: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    if not wallet_id:
        frappe.throw("wallet_id is required", frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    # Reporting must exclude soft-deleted rows unless explicitly requested.
    filters = ["tx.wallet_id = %(wallet_id)s", "tx.is_deleted = 0", "alloc.is_deleted = 0"]
    params: Dict[str, Any] = {"wallet_id": wallet_id}

    if from_date:
        filters.append("tx.date_time >= %(from_date)s")
        params["from_date"] = get_datetime(from_date)
    if to_date:
        filters.append("tx.date_time <= %(to_date)s")
        params["to_date"] = get_datetime(to_date)
    if currency:
        filters.append("alloc.currency = %(currency)s")
        params["currency"] = currency

    income_sql = f"""
        SELECT alloc.bucket as bucket, alloc.currency as currency, SUM(alloc.amount) as income_allocated
        FROM `tabHisabi Transaction Allocation` alloc
        INNER JOIN `tabHisabi Transaction` tx ON tx.name = alloc.transaction
        WHERE {' AND '.join(filters)} AND tx.transaction_type = 'income'
        GROUP BY alloc.bucket, alloc.currency
    """

    income_rows = frappe.db.sql(income_sql, params, as_dict=True)

    expense_filters = ["tx.wallet_id = %(wallet_id)s", "tx.is_deleted = 0", "alloc.is_deleted = 0"]
    if from_date:
        expense_filters.append("tx.date_time >= %(from_date)s")
    if to_date:
        expense_filters.append("tx.date_time <= %(to_date)s")
    if currency:
        expense_filters.append("alloc.currency = %(currency)s")

    expense_alloc_sql = f"""
        SELECT alloc.bucket as bucket, alloc.currency as currency, SUM(alloc.amount) as expense_spent
        FROM `tabHisabi Transaction Allocation` alloc
        INNER JOIN `tabHisabi Transaction` tx ON tx.name = alloc.transaction
        WHERE {' AND '.join(expense_filters)} AND tx.transaction_type = 'expense'
        GROUP BY alloc.bucket, alloc.currency
    """

    expense_alloc_rows = frappe.db.sql(expense_alloc_sql, params, as_dict=True)

    bucket_link_filters = ["tx.wallet_id = %(wallet_id)s", "tx.is_deleted = 0", "tx.bucket IS NOT NULL", "tx.bucket != ''"]
    if from_date:
        bucket_link_filters.append("tx.date_time >= %(from_date)s")
    if to_date:
        bucket_link_filters.append("tx.date_time <= %(to_date)s")
    if currency:
        bucket_link_filters.append("tx.currency = %(currency)s")

    bucket_link_sql = f"""
        SELECT tx.bucket as bucket, tx.currency as currency, SUM(tx.amount) as expense_spent
        FROM `tabHisabi Transaction` tx
        WHERE {' AND '.join(bucket_link_filters)}
          AND tx.transaction_type = 'expense'
          AND NOT EXISTS (
            SELECT 1 FROM `tabHisabi Transaction Allocation` alloc
            WHERE alloc.transaction = tx.name AND alloc.is_deleted = 0
          )
        GROUP BY tx.bucket, tx.currency
    """

    bucket_link_rows = frappe.db.sql(bucket_link_sql, params, as_dict=True)

    buckets = frappe.get_all(
        "Hisabi Bucket",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "bucket_name"],
    )
    bucket_map = {row.name: row.bucket_name for row in buckets}

    summary: Dict[tuple, Dict[str, Any]] = {}

    for row in income_rows:
        key = (row.bucket, row.currency)
        summary.setdefault(key, {"bucket": row.bucket, "currency": row.currency, "income_allocated": 0, "expense_spent": 0})
        summary[key]["income_allocated"] = row.income_allocated or 0

    for row in expense_alloc_rows:
        key = (row.bucket, row.currency)
        summary.setdefault(key, {"bucket": row.bucket, "currency": row.currency, "income_allocated": 0, "expense_spent": 0})
        summary[key]["expense_spent"] += row.expense_spent or 0

    for row in bucket_link_rows:
        key = (row.bucket, row.currency)
        summary.setdefault(key, {"bucket": row.bucket, "currency": row.currency, "income_allocated": 0, "expense_spent": 0})
        summary[key]["expense_spent"] += row.expense_spent or 0

    result = []
    for key, row in summary.items():
        bucket_id = row["bucket"]
        row["bucket_name"] = bucket_map.get(bucket_id, bucket_id)
        row["balance"] = (row.get("income_allocated") or 0) - (row.get("expense_spent") or 0)
        result.append(row)

    return {"buckets": result, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def bucket_rules(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    if not wallet_id:
        frappe.throw("wallet_id is required", frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    rules = frappe.get_all(
        "Hisabi Allocation Rule",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "scope_type", "scope_ref", "is_default", "active"],
        order_by="server_modified desc, doc_version desc",
    )
    for rule in rules:
        rule["rule"] = rule.get("name")

    rule_map = {rule.name: rule for rule in rules}
    lines = frappe.get_all(
        "Hisabi Allocation Rule Line",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["rule", "bucket", "percent", "sort_order"],
        order_by="sort_order asc, server_modified desc",
    )

    for rule in rules:
        rule["lines"] = []

    for line in lines:
        rule = rule_map.get(line.rule)
        if not rule:
            continue
        rule["lines"].append({
            "bucket": line.bucket,
            "percent": line.percent,
            "sort_order": line.sort_order,
        })

    return {"rules": rules}
