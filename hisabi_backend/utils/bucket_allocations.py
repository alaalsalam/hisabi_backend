"""Bucket allocation validation helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence

import frappe
from frappe.utils import cint, flt
from werkzeug.wrappers import Response

INVALID_BUCKET_ALLOCATION_CODE = "invalid_bucket_allocation"
INVALID_BUCKET_ALLOCATION_MESSAGE = "Allocations must sum to transaction value."
INVALID_BUCKET_EXPENSE_ASSIGNMENT_CODE = "invalid_bucket_expense_assignment"
INVALID_BUCKET_EXPENSE_ASSIGNMENT_MESSAGE = "Expense bucket assignment is invalid."
AMOUNT_EPSILON = 0.01
PERCENT_EPSILON = 0.0001


class InvalidBucketAllocationError(frappe.ValidationError):
    """Raised when bucket allocations are invalid."""

    def __init__(self, message: str | None = None):
        super().__init__(message or INVALID_BUCKET_ALLOCATION_MESSAGE)


def raise_invalid_bucket_allocation(message: str | None = None) -> None:
    raise InvalidBucketAllocationError(message)


class InvalidBucketExpenseAssignmentError(frappe.ValidationError):
    """Raised when an expense bucket assignment is invalid."""

    def __init__(self, message: str | None = None):
        super().__init__(message or INVALID_BUCKET_EXPENSE_ASSIGNMENT_MESSAGE)


def raise_invalid_bucket_expense_assignment(message: str | None = None) -> None:
    raise InvalidBucketExpenseAssignmentError(message)


def build_invalid_bucket_allocation_response(message: str | None = None) -> Response:
    import json

    payload = {
        "error": {
            "code": INVALID_BUCKET_ALLOCATION_CODE,
            "message": message or INVALID_BUCKET_ALLOCATION_MESSAGE,
        }
    }
    response = Response()
    response.mimetype = "application/json"
    response.status_code = 422
    response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return response


def build_invalid_bucket_expense_assignment_response(message: str | None = None) -> Response:
    import json

    payload = {
        "error": {
            "code": INVALID_BUCKET_EXPENSE_ASSIGNMENT_CODE,
            "message": message or INVALID_BUCKET_EXPENSE_ASSIGNMENT_MESSAGE,
        }
    }
    response = Response()
    response.mimetype = "application/json"
    response.status_code = 422
    response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return response


def sync_bucket_display_fields(doc) -> None:
    """Keep legacy and new bucket fields consistent."""
    title = (doc.get("title") or "").strip()
    bucket_name = (doc.get("bucket_name") or "").strip()
    archived = doc.get("archived")
    is_active = doc.get("is_active")

    if title and not bucket_name:
        doc.bucket_name = title
    elif bucket_name and not title:
        doc.title = bucket_name

    if is_active in (None, "") and archived not in (None, ""):
        doc.is_active = 0 if cint(archived or 0) else 1
    if archived in (None, "") and is_active not in (None, ""):
        doc.archived = 0 if cint(is_active or 0) else 1


def _ensure_transaction_type(
    transaction_id: str,
    wallet_id: str,
    *,
    expected_type: str,
    not_found_message: str,
    deleted_message: str,
    wallet_message: str,
    type_message: str,
) -> frappe.model.document.Document:
    tx_name = frappe.get_value("Hisabi Transaction", transaction_id, "name")
    if not tx_name:
        tx_name = frappe.get_value(
            "Hisabi Transaction",
            {"client_id": transaction_id, "wallet_id": wallet_id},
            "name",
        )
    if not tx_name:
        frappe.throw(not_found_message, frappe.ValidationError)
    tx = frappe.get_doc("Hisabi Transaction", tx_name)
    if tx.is_deleted:
        frappe.throw(deleted_message, frappe.ValidationError)
    if tx.wallet_id != wallet_id:
        frappe.throw(wallet_message, frappe.PermissionError)
    if tx.transaction_type != expected_type:
        frappe.throw(type_message, frappe.ValidationError)
    return tx


def ensure_income_transaction(transaction_id: str, wallet_id: str) -> frappe.model.document.Document:
    try:
        return _ensure_transaction_type(
            transaction_id,
            wallet_id,
            expected_type="income",
            not_found_message="Transaction not found.",
            deleted_message="Transaction is deleted.",
            wallet_message="Transaction is not in this wallet.",
            type_message="Bucket allocation is only allowed for income transactions.",
        )
    except frappe.PermissionError as exc:
        raise_invalid_bucket_allocation(str(exc))
    except frappe.ValidationError as exc:
        raise_invalid_bucket_allocation(str(exc))


def ensure_expense_transaction(transaction_id: str, wallet_id: str) -> frappe.model.document.Document:
    try:
        return _ensure_transaction_type(
            transaction_id,
            wallet_id,
            expected_type="expense",
            not_found_message="Transaction not found.",
            deleted_message="Transaction is deleted.",
            wallet_message="Transaction is not in this wallet.",
            type_message="Bucket expense assignment is only allowed for expense transactions.",
        )
    except frappe.PermissionError as exc:
        raise_invalid_bucket_expense_assignment(str(exc))
    except frappe.ValidationError as exc:
        raise_invalid_bucket_expense_assignment(str(exc))


def ensure_bucket_wallet_scope(
    bucket_id: str,
    wallet_id: str,
    *,
    raise_error: Callable[[str | None], None] = raise_invalid_bucket_allocation,
) -> frappe.model.document.Document:
    bucket_name = frappe.get_value("Hisabi Bucket", bucket_id, "name")
    if not bucket_name:
        bucket_name = frappe.get_value(
            "Hisabi Bucket",
            {"client_id": bucket_id, "wallet_id": wallet_id},
            "name",
        )
    if not bucket_name:
        raise_error("Bucket not found.")
    bucket = frappe.get_doc("Hisabi Bucket", bucket_name)
    if bucket.is_deleted:
        raise_error("Bucket is deleted.")
    if bucket.wallet_id != wallet_id:
        raise_error("Bucket does not belong to this wallet.")
    is_active = bucket.get("is_active")
    if is_active in (None, ""):
        is_active = 0 if cint(bucket.get("archived") or 0) else 1
    if cint(is_active) == 0:
        raise_error("Inactive bucket cannot receive allocations.")
    return bucket


def ensure_wallet_scoped_buckets(bucket_ids: Sequence[str], wallet_id: str) -> None:
    wanted = {bucket_id for bucket_id in bucket_ids if bucket_id}
    if not wanted:
        raise_invalid_bucket_allocation("Bucket is required.")
    rows = frappe.get_all(
        "Hisabi Bucket",
        filters={"name": ["in", sorted(wanted)], "wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "is_active", "archived"],
    )
    if len(rows) != len(wanted):
        raise_invalid_bucket_allocation("Bucket does not belong to this wallet.")
    for row in rows:
        is_active = row.get("is_active")
        if is_active in (None, ""):
            is_active = 0 if cint(row.get("archived") or 0) else 1
        if cint(is_active) == 0:
            raise_invalid_bucket_allocation("Inactive bucket cannot receive allocations.")


def normalize_manual_allocations(
    *,
    tx_amount: float,
    mode: str,
    allocations: Sequence[Dict[str, Any]],
) -> List[Dict[str, float | str]]:
    if not allocations:
        raise_invalid_bucket_allocation("Allocations are required.")

    total_amount = flt(tx_amount, 2)
    if total_amount <= 0:
        raise_invalid_bucket_allocation("Transaction amount must be positive.")

    mode_normalized = (mode or "").strip().lower()
    if mode_normalized not in {"percent", "amount"}:
        raise_invalid_bucket_allocation("Invalid allocation mode.")

    rows: List[Dict[str, float | str]] = []
    if mode_normalized == "percent":
        total_percent = 0.0
        for row in allocations:
            bucket_id = str(row.get("bucket") or "").strip()
            if not bucket_id:
                raise_invalid_bucket_allocation("Bucket is required.")
            percentage = flt(row.get("value"), 6)
            if percentage <= 0 or percentage > 100:
                raise_invalid_bucket_allocation("Percentage must be between 0 and 100.")
            total_percent += percentage
            rows.append(
                {
                    "bucket": bucket_id,
                    "percentage": percentage,
                    "amount": flt(total_amount * (percentage / 100), 2),
                }
            )

        if abs(total_percent - 100.0) > PERCENT_EPSILON:
            raise_invalid_bucket_allocation()

        allocated = flt(sum(flt(row["amount"], 2) for row in rows), 2)
        remainder = flt(total_amount - allocated, 2)
        if rows and abs(remainder) > AMOUNT_EPSILON:
            largest = max(rows, key=lambda row: flt(row["amount"], 2))
            largest["amount"] = flt(flt(largest["amount"], 2) + remainder, 2)

    if mode_normalized == "amount":
        allocated = 0.0
        for row in allocations:
            bucket_id = str(row.get("bucket") or "").strip()
            if not bucket_id:
                raise_invalid_bucket_allocation("Bucket is required.")
            amount = flt(row.get("value"), 2)
            if amount <= 0:
                raise_invalid_bucket_allocation("Allocation amount must be positive.")
            allocated += amount
            rows.append({"bucket": bucket_id, "amount": amount})

        if abs(flt(allocated, 2) - total_amount) > AMOUNT_EPSILON:
            raise_invalid_bucket_allocation()

        for row in rows:
            row["percentage"] = flt((flt(row["amount"], 6) / total_amount) * 100, 6)

    return rows


def normalize_transaction_bucket_row(doc) -> None:
    """Normalize alias fields on transaction-bucket documents."""
    if doc.get("transaction") and not doc.get("transaction_id"):
        doc.transaction_id = doc.get("transaction")
    if doc.get("bucket") and not doc.get("bucket_id"):
        doc.bucket_id = doc.get("bucket")
    if doc.get("percent") not in (None, "") and doc.get("percentage") in (None, ""):
        doc.percentage = doc.get("percent")

    if doc.get("wallet_id") in (None, "") and doc.get("transaction_id"):
        doc.wallet_id = frappe.get_value("Hisabi Transaction", doc.get("transaction_id"), "wallet_id")

    wallet_id = doc.get("wallet_id")
    transaction_id = doc.get("transaction_id")
    bucket_id = doc.get("bucket_id")

    if doc.get("is_deleted"):
        return

    if not transaction_id:
        raise_invalid_bucket_allocation("transaction_id is required.")
    if not bucket_id:
        raise_invalid_bucket_allocation("bucket_id is required.")
    if not wallet_id:
        raise_invalid_bucket_allocation("wallet_id is required.")

    tx_doc = ensure_income_transaction(transaction_id, wallet_id)
    ensure_bucket_wallet_scope(bucket_id, wallet_id)

    amount = doc.get("amount")
    percentage = doc.get("percentage")

    has_amount = amount not in (None, "")
    has_percentage = percentage not in (None, "")
    if not has_amount and not has_percentage:
        raise_invalid_bucket_allocation("Either amount or percentage is required.")

    if has_amount:
        amount = flt(amount, 2)
        if amount <= 0:
            raise_invalid_bucket_allocation("Allocation amount must be positive.")
        if amount - flt(tx_doc.amount, 2) > AMOUNT_EPSILON:
            raise_invalid_bucket_allocation("Allocation amount cannot exceed transaction amount.")
        doc.amount = amount

    if has_percentage:
        percentage = flt(percentage, 6)
        if percentage <= 0 or percentage > 100:
            raise_invalid_bucket_allocation("Allocation percentage must be between 0 and 100.")
        doc.percentage = percentage

    tx_amount = flt(tx_doc.amount, 2)
    if not has_amount and has_percentage:
        doc.amount = flt(tx_amount * (flt(doc.percentage, 6) / 100), 2)
    if has_amount and not has_percentage and tx_amount > 0:
        doc.percentage = flt((flt(doc.amount, 6) / tx_amount) * 100, 6)

    if has_amount and has_percentage:
        expected = flt(tx_amount * (flt(doc.percentage, 6) / 100), 2)
        if abs(expected - flt(doc.amount, 2)) > AMOUNT_EPSILON:
            raise_invalid_bucket_allocation("Allocation amount and percentage are inconsistent.")


def normalize_transaction_bucket_expense_row(doc) -> None:
    """Normalize alias fields on transaction-expense-bucket documents."""
    if doc.get("transaction") and not doc.get("transaction_id"):
        doc.transaction_id = doc.get("transaction")
    if doc.get("bucket") and not doc.get("bucket_id"):
        doc.bucket_id = doc.get("bucket")

    if doc.get("wallet_id") in (None, "") and doc.get("transaction_id"):
        doc.wallet_id = frappe.get_value("Hisabi Transaction", doc.get("transaction_id"), "wallet_id")

    wallet_id = doc.get("wallet_id")
    transaction_id = doc.get("transaction_id")
    bucket_id = doc.get("bucket_id")

    if doc.get("is_deleted"):
        return

    if not transaction_id:
        raise_invalid_bucket_expense_assignment("transaction_id is required.")
    if not bucket_id:
        raise_invalid_bucket_expense_assignment("bucket_id is required.")
    if not wallet_id:
        raise_invalid_bucket_expense_assignment("wallet_id is required.")

    ensure_expense_transaction(transaction_id, wallet_id)
    ensure_bucket_wallet_scope(
        bucket_id,
        wallet_id,
        raise_error=raise_invalid_bucket_expense_assignment,
    )
