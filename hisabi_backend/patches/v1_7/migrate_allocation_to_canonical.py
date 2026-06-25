"""Backfill Hisabi Transaction Bucket with currency/amount_base/is_manual_override/rule_used
from the legacy Hisabi Transaction Allocation table."""

import frappe


def execute():
    if not frappe.db.exists("DocType", "Hisabi Transaction Allocation"):
        return
    if not frappe.db.table_exists("tabHisabi Transaction Allocation"):
        return

    rows = frappe.db.sql("""
        SELECT a.transaction, a.bucket, a.currency, a.amount_base,
               a.is_manual_override, a.rule_used
        FROM `tabHisabi Transaction Allocation` a
        WHERE a.is_deleted = 0
          AND a.currency IS NOT NULL
    """, as_dict=True)

    for row in rows:
        bucket_name = frappe.db.get_value(
            "Hisabi Transaction Bucket",
            {"transaction_id": row.transaction, "bucket_id": row.bucket, "is_deleted": 0},
            "name",
        )
        if not bucket_name:
            continue
        frappe.db.set_value(
            "Hisabi Transaction Bucket",
            bucket_name,
            {
                "currency": row.currency,
                "amount_base": row.amount_base,
                "is_manual_override": row.is_manual_override or 0,
                "rule_used": row.rule_used,
            },
        )
