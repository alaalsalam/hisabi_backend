"""DocType controller for Hisabi Custom Currency."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class HisabiCustomCurrency(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
