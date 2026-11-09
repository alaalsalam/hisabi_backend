"""DocType controller for Hisabi Notification (user-scoped inbox)."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class HisabiNotification(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
