"""Allocation engine for bucket rules and transaction allocations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import flt


@dataclass
class AllocationRow:
    bucket: str
    percent: int
    amount: float
    currency: str
    amount_base: float
    rule_used: Optional[str]
    is_manual_override: int


def _hard_delete_allocations(transaction_id: str, *, manual_only: Optional[bool] = None) -> None:
    filters: Dict[str, object] = {"transaction": transaction_id}
    if manual_only is True:
        filters["is_manual_override"] = 1
    if manual_only is False:
        filters["is_manual_override"] = 0
    frappe.db.delete("Hisabi Transaction Allocation", filters)


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

    rows_sorted = sorted(rows, key=lambda r: (r.percent, r.amount), reverse=True)
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

    has_manual = frappe.db.exists(
        "Hisabi Transaction Allocation",
        {"transaction": tx_doc.name, "is_manual_override": 1},
    )
    if has_manual:
        return

    allocations = generate_allocations(tx_doc.user, tx_doc)
    _hard_delete_allocations(tx_doc.name, manual_only=False)

    if not allocations:
        return

    for row in allocations:
        client_id = f"{tx_doc.client_id}:{row.bucket}"
        alloc = frappe.new_doc("Hisabi Transaction Allocation")
        alloc.user = tx_doc.user
        alloc.transaction = tx_doc.name
        alloc.bucket = row.bucket
        alloc.percent = row.percent
        alloc.amount = row.amount
        alloc.currency = row.currency
        alloc.amount_base = row.amount_base
        alloc.rule_used = row.rule_used
        alloc.is_manual_override = row.is_manual_override
        alloc.client_id = client_id
        alloc.name = client_id
        alloc.insert(ignore_permissions=True)


def set_manual_allocations(
    *,
    user: str,
    tx_doc,
    mode: str,
    allocations: List[Dict[str, object]],
) -> List[AllocationRow]:
    """Replace allocations with manual overrides and return rows."""
    if not allocations:
        frappe.throw(_("allocations required"), frappe.ValidationError)

    if tx_doc.is_deleted:
        frappe.throw(_("Transaction is deleted"), frappe.ValidationError)

    total_amount = flt(tx_doc.amount, 2)
    if total_amount <= 0:
        frappe.throw(_("Transaction amount must be positive"), frappe.ValidationError)

    rows: List[AllocationRow] = []
    if mode == "percent":
        total_percent = 0
        for alloc in allocations:
            percent = int(alloc.get("value") or 0)
            if percent <= 0 or percent > 100:
                frappe.throw(_("Percent must be between 1 and 100"), frappe.ValidationError)
            total_percent += percent
            amount = flt(total_amount * (percent / 100), 2)
            rows.append(
                AllocationRow(
                    bucket=str(alloc.get("bucket")),
                    percent=percent,
                    amount=amount,
                    currency=tx_doc.currency,
                    amount_base=amount,
                    rule_used=None,
                    is_manual_override=1,
                )
            )
        if total_percent > 100:
            frappe.throw(_("Total percent cannot exceed 100"), frappe.ValidationError)
        _reconcile_amounts(rows, total_amount)
    elif mode == "amount":
        total_value = 0
        for alloc in allocations:
            value = flt(alloc.get("value"), 2)
            if value <= 0:
                frappe.throw(_("Allocation amount must be positive"), frappe.ValidationError)
            total_value += value
            percent = int(round((value / total_amount) * 100))
            rows.append(
                AllocationRow(
                    bucket=str(alloc.get("bucket")),
                    percent=percent,
                    amount=value,
                    currency=tx_doc.currency,
                    amount_base=value,
                    rule_used=None,
                    is_manual_override=1,
                )
            )
        if total_value > total_amount:
            frappe.throw(_("Total allocation cannot exceed transaction amount"), frappe.ValidationError)
        _reconcile_amounts(rows, total_amount)
    else:
        frappe.throw(_("Invalid mode"), frappe.ValidationError)

    # Delete all existing allocations (manual or auto)
    _hard_delete_allocations(tx_doc.name)

    for row in rows:
        client_id = f"{tx_doc.client_id}:{row.bucket}:manual"
        alloc = frappe.new_doc("Hisabi Transaction Allocation")
        alloc.user = user
        alloc.transaction = tx_doc.name
        alloc.bucket = row.bucket
        alloc.percent = row.percent
        alloc.amount = row.amount
        alloc.currency = row.currency
        alloc.amount_base = row.amount_base
        alloc.rule_used = row.rule_used
        alloc.is_manual_override = 1
        alloc.client_id = client_id
        alloc.name = client_id
        alloc.insert(ignore_permissions=True)

    return rows
