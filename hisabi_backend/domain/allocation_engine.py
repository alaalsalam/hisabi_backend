"""Allocation engine for bucket rules and transaction allocations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import frappe
from frappe.utils import flt

from hisabi_backend.utils.bucket_allocations import (
    ensure_income_transaction,
    ensure_wallet_scoped_buckets,
    normalize_manual_allocations,
)


@dataclass
class AllocationRow:
    bucket: str
    percent: float
    amount: float
    currency: str
    amount_base: float
    rule_used: Optional[str]
    is_manual_override: int


def _allocation_doctypes() -> List[str]:
    doctypes: List[str] = []
    for doctype in ("Hisabi Transaction Allocation", "Hisabi Transaction Bucket"):
        if frappe.db.exists("DocType", doctype):
            doctypes.append(doctype)
    return doctypes


def _hard_delete_allocations(transaction_id: str, *, manual_only: Optional[bool] = None) -> None:
    if frappe.db.exists("DocType", "Hisabi Transaction Allocation"):
        legacy_filters: Dict[str, object] = {"transaction": transaction_id}
        if manual_only is True:
            legacy_filters["is_manual_override"] = 1
        if manual_only is False:
            legacy_filters["is_manual_override"] = 0
        frappe.db.delete("Hisabi Transaction Allocation", legacy_filters)

    if frappe.db.exists("DocType", "Hisabi Transaction Bucket"):
        bucket_filters: Dict[str, object] = {"transaction_id": transaction_id}
        if manual_only is True:
            bucket_filters["client_id"] = ["like", "%:manual"]
        elif manual_only is False:
            bucket_filters["client_id"] = ["not like", "%:manual"]
        frappe.db.delete("Hisabi Transaction Bucket", bucket_filters)


def _has_manual_allocations(transaction_id: str) -> bool:
    if frappe.db.exists(
        "Hisabi Transaction Allocation",
        {"transaction": transaction_id, "is_manual_override": 1},
    ):
        return True
    if frappe.db.exists(
        "Hisabi Transaction Bucket",
        {"transaction_id": transaction_id, "client_id": ["like", "%:manual"], "is_deleted": 0},
    ):
        return True
    return False


def _insert_allocation_row(
    *,
    doctype: str,
    user: str,
    tx_doc,
    row: AllocationRow,
    client_id: str,
) -> None:
    alloc = frappe.new_doc(doctype)
    alloc.user = user
    alloc.wallet_id = tx_doc.wallet_id
    alloc.client_id = client_id
    alloc.name = client_id

    if doctype == "Hisabi Transaction Allocation":
        alloc.transaction = tx_doc.name
        alloc.bucket = row.bucket
        alloc.percent = int(round(flt(row.percent, 6)))
        alloc.amount = flt(row.amount, 2)
        alloc.currency = row.currency
        alloc.amount_base = flt(row.amount_base, 2)
        alloc.rule_used = row.rule_used
        alloc.is_manual_override = row.is_manual_override
    else:
        alloc.transaction_id = tx_doc.name
        alloc.bucket_id = row.bucket
        alloc.amount = flt(row.amount, 2)
        alloc.percentage = flt(row.percent, 6)

    alloc.insert(ignore_permissions=True)


def resolve_rule(user: str, tx_doc) -> Optional[frappe.model.document.Document]:
    """Resolve allocation rule according to priority."""
    if not tx_doc or tx_doc.is_deleted:
        return None

    candidates = []

    if tx_doc.account:
        candidates = frappe.get_all(
            "Hisabi Allocation Rule",
            filters={
                "user": user,
                "active": 1,
                "is_deleted": 0,
                "scope_type": "by_account",
                "scope_ref": tx_doc.account,
            },
            order_by="server_modified desc, doc_version desc",
            limit=1,
        )
    if candidates:
        return frappe.get_doc("Hisabi Allocation Rule", candidates[0].name)

    if tx_doc.category:
        candidates = frappe.get_all(
            "Hisabi Allocation Rule",
            filters={
                "user": user,
                "active": 1,
                "is_deleted": 0,
                "scope_type": "by_income_category",
                "scope_ref": tx_doc.category,
            },
            order_by="server_modified desc, doc_version desc",
            limit=1,
        )
    if candidates:
        return frappe.get_doc("Hisabi Allocation Rule", candidates[0].name)

    candidates = frappe.get_all(
        "Hisabi Allocation Rule",
        filters={
            "user": user,
            "active": 1,
            "is_deleted": 0,
            "scope_type": "global",
            "is_default": 1,
        },
        order_by="server_modified desc, doc_version desc",
        limit=1,
    )
    if candidates:
        return frappe.get_doc("Hisabi Allocation Rule", candidates[0].name)

    return None


def _fetch_rule_lines(rule_name: str, user: str) -> List[Dict[str, object]]:
    return frappe.get_all(
        "Hisabi Allocation Rule Line",
        filters={
            "rule": rule_name,
            "user": user,
            "is_deleted": 0,
        },
        fields=["bucket", "percent", "sort_order"],
        order_by="sort_order asc, server_modified desc, doc_version desc",
    )


def _reconcile_amounts(rows: List[AllocationRow], total_amount: float) -> None:
    if not rows:
        return

    total_alloc = sum(flt(row.amount, 2) for row in rows)
    remainder = flt(total_amount - total_alloc, 2)
    if remainder == 0:
        return

    rows_sorted = sorted(rows, key=lambda r: (flt(r.percent, 6), flt(r.amount, 2)), reverse=True)
    target = rows_sorted[0]
    target.amount = flt(target.amount + remainder, 2)
    target.amount_base = target.amount


def generate_allocations(user: str, tx_doc) -> List[AllocationRow]:
    """Generate allocations for an income transaction."""
    if not tx_doc or tx_doc.transaction_type != "income" or tx_doc.is_deleted:
        return []

    rule = resolve_rule(user, tx_doc)
    if not rule:
        return []

    lines = _fetch_rule_lines(rule.name, user)
    if not lines:
        return []

    rows: List[AllocationRow] = []
    for line in lines:
        percent = int(line.get("percent") or 0)
        if percent <= 0:
            continue
        amount = flt(tx_doc.amount * (percent / 100), 2)
        rows.append(
            AllocationRow(
                bucket=line["bucket"],
                percent=percent,
                amount=amount,
                currency=tx_doc.currency,
                amount_base=amount,
                rule_used=rule.name,
                is_manual_override=0,
            )
        )

    _reconcile_amounts(rows, flt(tx_doc.amount, 2))
    return rows


def apply_auto_allocations(tx_doc) -> None:
    """Apply auto allocations for income transactions and clean up on delete."""
    if not tx_doc:
        return

    if tx_doc.is_deleted:
        _hard_delete_allocations(tx_doc.name)
        return

    if tx_doc.transaction_type != "income":
        _hard_delete_allocations(tx_doc.name, manual_only=False)
        return

    if _has_manual_allocations(tx_doc.name):
        return

    allocations = generate_allocations(tx_doc.user, tx_doc)
    _hard_delete_allocations(tx_doc.name, manual_only=False)

    if not allocations:
        return

    for row in allocations:
        client_id = f"{tx_doc.client_id}:{row.bucket}"
        for doctype in _allocation_doctypes():
            _insert_allocation_row(
                doctype=doctype,
                user=tx_doc.user,
                tx_doc=tx_doc,
                row=row,
                client_id=client_id,
            )


def set_manual_allocations(
    *,
    user: str,
    tx_doc,
    mode: str,
    allocations: List[Dict[str, object]],
) -> List[AllocationRow]:
    """Replace allocations with manual overrides and return rows."""
    tx_doc = ensure_income_transaction(tx_doc.name, tx_doc.wallet_id)

    normalized_rows = normalize_manual_allocations(
        tx_amount=flt(tx_doc.amount, 2),
        mode=mode,
        allocations=allocations,
    )
    ensure_wallet_scoped_buckets([str(row.get("bucket")) for row in normalized_rows], tx_doc.wallet_id)

    rows: List[AllocationRow] = [
        AllocationRow(
            bucket=str(row.get("bucket")),
            percent=flt(row.get("percentage"), 6),
            amount=flt(row.get("amount"), 2),
            currency=tx_doc.currency,
            amount_base=flt(row.get("amount"), 2),
            rule_used=None,
            is_manual_override=1,
        )
        for row in normalized_rows
    ]

    # Delete all existing allocations (manual or auto)
    _hard_delete_allocations(tx_doc.name)

    for row in rows:
        client_id = f"{tx_doc.client_id}:{row.bucket}:manual"
        for doctype in _allocation_doctypes():
            _insert_allocation_row(
                doctype=doctype,
                user=user,
                tx_doc=tx_doc,
                row=row,
                client_id=client_id,
            )

    return rows
