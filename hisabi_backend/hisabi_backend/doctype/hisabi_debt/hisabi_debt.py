"""DocType controller for Hisabi Debt."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from hisabi_backend.utils.validators import validate_currency


class HisabiDebt(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if flt(self.principal_amount) <= 0:
            frappe.throw(_("principal_amount must be greater than 0"), frappe.ValidationError)

        if self.direction and self.direction not in {"owe", "owed_to_me"}:
            frappe.throw(_("Invalid direction"), frappe.ValidationError)

        if self.currency:
            self.currency = validate_currency(self.currency, self.user)

        if self.remaining_amount is None:
            self.remaining_amount = self.principal_amount

        if self.remaining_amount is not None and flt(self.remaining_amount) > flt(self.principal_amount):
            frappe.throw(_("remaining_amount cannot exceed principal_amount"), frappe.ValidationError)

        if not self.status:
            self.status = "active"

        if self.status == "closed":
            self.remaining_amount = 0
