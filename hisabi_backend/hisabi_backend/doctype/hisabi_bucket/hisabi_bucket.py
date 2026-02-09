"""DocType controller for Hisabi Bucket."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from hisabi_backend.utils.bucket_allocations import sync_bucket_display_fields


class HisabiBucket(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        sync_bucket_display_fields(self)
        if not (self.title or self.bucket_name):
            frappe.throw(_("title is required"), frappe.ValidationError)
