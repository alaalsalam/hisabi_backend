"""DocType controller for Hisabi Allocation Rule Line."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class HisabiAllocationRuleLine(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        if not self.rule or self.is_deleted:
            return

        if not self.percent or self.percent <= 0 or self.percent > 100:
            frappe.throw(_("Percent must be between 1 and 100"))

        rule_user = frappe.get_value("Hisabi Allocation Rule", self.rule, "user")
        if rule_user and self.user and rule_user != self.user:
            frappe.throw(_("Rule does not belong to user"), frappe.PermissionError)

        bucket_user = frappe.get_value("Hisabi Bucket", self.bucket, "user")
        if bucket_user and self.user and bucket_user != self.user:
            frappe.throw(_("Bucket does not belong to user"), frappe.PermissionError)

        if frappe.db.exists(
            "Hisabi Allocation Rule Line",
            {
                "rule": self.rule,
                "bucket": self.bucket,
                "name": ["!=", self.name],
                "is_deleted": 0,
            },
        ):
            frappe.throw(_("Duplicate bucket in allocation rule"), frappe.ValidationError)

        total = 0
        lines = frappe.get_all(
            "Hisabi Allocation Rule Line",
            filters={
                "rule": self.rule,
                "is_deleted": 0,
                "user": self.user,
            },
            fields=["name", "percent"],
        )
        for line in lines:
            if line.name == self.name:
                continue
            total += line.percent or 0

        total += self.percent or 0
        if total > 100:
            frappe.throw(_("Allocation percent total cannot exceed 100"))
