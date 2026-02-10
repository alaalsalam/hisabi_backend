"""DocType controller for Hisabi Transaction Bucket Expense."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from hisabi_backend.utils.bucket_allocations import (
    normalize_transaction_bucket_expense_row,
    raise_invalid_bucket_expense_assignment,
)


class HisabiTransactionBucketExpense(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if not self.client_id:
            self.client_id = f"tx-exp-bucket-{frappe.generate_hash(length=12)}"
        self.name = self.client_id
        self.flags.name_set = True

    def validate(self):
        normalize_transaction_bucket_expense_row(self)
        if self.get("is_deleted"):
            return

        existing = frappe.get_value(
            "Hisabi Transaction Bucket Expense",
            {
                "wallet_id": self.wallet_id,
                "transaction_id": self.transaction_id,
                "is_deleted": 0,
                "name": ["!=", self.name],
            },
            "name",
        )
        if existing:
            raise_invalid_bucket_expense_assignment(
                "Only one bucket assignment is allowed per expense transaction."
            )
