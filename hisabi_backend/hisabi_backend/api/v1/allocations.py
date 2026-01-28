"""Allocation APIs for manual overrides and rebuilds."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import get_datetime

from hisabi_backend.domain.allocation_engine import apply_auto_allocations, set_manual_allocations
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


@frappe.whitelist(allow_guest=False)
def set_manual_allocations(
    transaction_id: str,
    mode: str,
    allocations: List[Dict[str, Any]],
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Set manual allocations for a transaction."""
    user, _device = require_device_token_auth()
    if not wallet_id:
        frappe.throw(_("wallet_id is required"), frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not transaction_id:
        frappe.throw(_("transaction_id is required"), frappe.ValidationError)

    tx = frappe.get_doc("Hisabi Transaction", transaction_id)
    if getattr(tx, "wallet_id", None) and tx.wallet_id != wallet_id:
        frappe.throw(_("Transaction is not in this wallet"), frappe.PermissionError)
    if tx.is_deleted:
        frappe.throw(_("Transaction is deleted"), frappe.ValidationError)

    bucket_ids = {row.get("bucket") for row in allocations}
    if None in bucket_ids:
        frappe.throw(_("Bucket is required"), frappe.ValidationError)

    buckets = frappe.get_all(
        "Hisabi Bucket",
        filters={"name": ["in", list(bucket_ids)], "wallet_id": wallet_id, "is_deleted": 0},
        pluck="name",
    )
    if len(buckets) != len(bucket_ids):
        frappe.throw(_("Invalid bucket in allocations"), frappe.ValidationError)

    rows = set_manual_allocations(user=user, tx_doc=tx, mode=mode, allocations=allocations)

    return {
        "status": "ok",
        "transaction_id": transaction_id,
        "allocations": [
            {
                "bucket": row.bucket,
                "percent": row.percent,
                "amount": row.amount,
                "currency": row.currency,
                "amount_base": row.amount_base,
                "is_manual_override": row.is_manual_override,
            }
            for row in rows
        ],
    }


@frappe.whitelist(allow_guest=False)
def rebuild_income_allocations(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Rebuild auto allocations for income transactions (non-manual only)."""
    user, _device = require_device_token_auth()
    if not wallet_id:
        frappe.throw(_("wallet_id is required"), frappe.ValidationError)
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    filters: Dict[str, Any] = {
        "wallet_id": wallet_id,
        "transaction_type": "income",
        "is_deleted": 0,
    }
    if from_date:
        filters["date_time"] = [">=", get_datetime(from_date)]
    if to_date:
        filters["date_time"] = ["<=", get_datetime(to_date)]

    transactions = frappe.get_all(
        "Hisabi Transaction",
        filters=filters,
        pluck="name",
    )

    for tx_name in transactions:
        tx_doc = frappe.get_doc("Hisabi Transaction", tx_name)
        apply_auto_allocations(tx_doc)

    return {"status": "ok", "count": len(transactions)}
