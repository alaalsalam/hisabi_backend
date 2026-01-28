"""DocType controller for Hisabi Category."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class HisabiCategory(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        if self.parent_category:
            parent = frappe.get_doc("Hisabi Category", self.parent_category)
            if parent.user != self.user:
                frappe.throw(_("Parent category must belong to same user"))
