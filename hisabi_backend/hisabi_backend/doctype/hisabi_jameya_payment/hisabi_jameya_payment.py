"""DocType controller for Hisabi Jameya Payment."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HisabiJameyaPayment(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("amount must be greater than 0"), frappe.ValidationError)
