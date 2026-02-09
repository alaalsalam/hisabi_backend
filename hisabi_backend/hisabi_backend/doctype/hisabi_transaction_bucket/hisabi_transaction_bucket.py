"""DocType controller for Hisabi Transaction Bucket."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from hisabi_backend.utils.bucket_allocations import normalize_transaction_bucket_row


class HisabiTransactionBucket(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        normalize_transaction_bucket_row(self)
