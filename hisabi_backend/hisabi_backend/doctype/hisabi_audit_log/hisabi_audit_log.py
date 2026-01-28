"""DocType controller for Hisabi Audit Log."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class HisabiAuditLog(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
