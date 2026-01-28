"""DocType controller for Hisabi Transaction."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from hisabi_backend.domain.allocation_engine import apply_auto_allocations

class HisabiTransaction(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        if self.transaction_type == "transfer" and self.account and self.to_account:
            if self.account == self.to_account:
                frappe.throw(_("Transfer account cannot match to_account"))

    def after_insert(self):
        apply_auto_allocations(self)

    def on_update(self):
        apply_auto_allocations(self)
