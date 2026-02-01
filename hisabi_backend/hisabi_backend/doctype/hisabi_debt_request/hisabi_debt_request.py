"""DocType controller for Hisabi Debt Request."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class HisabiDebtRequest(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if not self.status:
            self.status = "pending"

        if self.status == "declined":
            self.status = "rejected"

        if self.status not in {"pending", "accepted", "rejected"}:
            frappe.throw(_("Invalid status"), frappe.ValidationError)

        if self.debt_payload and not self.debt_payload_json:
            self.debt_payload_json = self.debt_payload
