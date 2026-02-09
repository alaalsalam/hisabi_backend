from __future__ import annotations

import frappe
from frappe.utils import flt


def execute() -> None:
    if not frappe.db.exists("DocType", "Hisabi Transaction Allocation"):
        return
    if not frappe.db.exists("DocType", "Hisabi Transaction Bucket"):
        return

    rows = frappe.get_all(
        "Hisabi Transaction Allocation",
        fields=[
            "name",
            "user",
            "wallet_id",
            "client_id",
            "transaction",
            "bucket",
            "amount",
            "percent",
            "client_created_ms",
            "client_modified_ms",
            "doc_version",
            "server_modified",
            "is_deleted",
            "deleted_at",
        ],
        limit_page_length=0,
    )
    if not rows:
        return

    for row in rows:
        if not row.client_id or not row.wallet_id or not row.transaction or not row.bucket:
            continue

        tx_meta = frappe.db.get_value(
            "Hisabi Transaction",
            row.transaction,
            ["transaction_type", "wallet_id", "is_deleted"],
            as_dict=True,
        )
        if not tx_meta:
            continue
        if tx_meta.transaction_type != "income" or int(tx_meta.is_deleted or 0) == 1:
            continue
        if tx_meta.wallet_id != row.wallet_id:
            continue

        existing = frappe.get_value(
            "Hisabi Transaction Bucket",
            {"client_id": row.client_id, "wallet_id": row.wallet_id},
            "name",
        )
        if existing:
            doc = frappe.get_doc("Hisabi Transaction Bucket", existing)
        else:
            doc = frappe.new_doc("Hisabi Transaction Bucket")
            doc.client_id = row.client_id
            doc.name = row.client_id
            doc.flags.name_set = True

        doc.user = row.user
        doc.wallet_id = row.wallet_id
        doc.transaction_id = row.transaction
        doc.bucket_id = row.bucket
        doc.amount = flt(row.amount, 2)
        doc.percentage = flt(row.percent, 6)
        doc.client_created_ms = row.client_created_ms
        doc.client_modified_ms = row.client_modified_ms
        doc.doc_version = row.doc_version or 0
        doc.server_modified = row.server_modified
        doc.is_deleted = row.is_deleted or 0
        doc.deleted_at = row.deleted_at
        try:
            doc.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                title="hisabi_backend.backfill_transaction_buckets",
                message=(
                    f"Failed to backfill transaction bucket for client_id={row.client_id}, "
                    f"wallet_id={row.wallet_id}"
                ),
            )
