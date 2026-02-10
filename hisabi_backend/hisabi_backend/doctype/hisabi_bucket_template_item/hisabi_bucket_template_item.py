"""DocType controller for Hisabi Bucket Template Item."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HisabiBucketTemplateItem(Document):
    def validate(self):
        if not self.bucket_id:
            frappe.throw(_("bucket_id is required"), frappe.ValidationError)

        percentage = flt(self.percentage, 6)
        if percentage <= 0 or percentage > 100:
            frappe.throw(_("Percentage must be between 0 and 100"), frappe.ValidationError)
