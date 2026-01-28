"""DocType controller for Hisabi FX Rate."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class HisabiFxRate(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
