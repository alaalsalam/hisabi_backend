"""DocType controller for Hisabi Sync Op."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class HisabiSyncOp(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
