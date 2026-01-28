"""DocType controller for Hisabi Goal."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from hisabi_backend.utils.validators import validate_currency


class HisabiGoal(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if self.goal_type and self.goal_type not in {"save", "pay_debt"}:
            frappe.throw(_("Invalid goal_type"), frappe.ValidationError)

        if self.goal_type == "pay_debt" and not self.linked_debt:
            frappe.throw(_("linked_debt is required for pay_debt goals"), frappe.ValidationError)

        if self.target_amount is not None and flt(self.target_amount) <= 0:
            frappe.throw(_("target_amount must be greater than 0"), frappe.ValidationError)

        if self.currency:
            self.currency = validate_currency(self.currency, self.user)

        if not self.status:
            self.status = "active"
