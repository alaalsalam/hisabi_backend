"""DocType controller for Hisabi Bucket."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from hisabi_backend.utils.bucket_allocations import sync_bucket_display_fields


class HisabiBucket(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        sync_bucket_display_fields(self)
        if not (self.title or self.bucket_name):
            frappe.throw(_("title is required"), frappe.ValidationError)
        self._validate_template_guardrails()

    def _validate_template_guardrails(self) -> None:
        if not self.wallet_id:
            return

        prev = self.get_doc_before_save() if not self.is_new() else None
        was_archived = cint(prev.archived) if prev else 0
        was_deleted = cint(prev.is_deleted) if prev else 0
        is_archiving = cint(self.archived) == 1 and was_archived == 0
        is_deleting = cint(self.is_deleted) == 1 and was_deleted == 0
        if not (is_archiving or is_deleting):
            return

        if not frappe.db.exists("DocType", "Hisabi Bucket Template"):
            return

        rows = frappe.db.sql(
            """
            SELECT DISTINCT t.name, t.title
            FROM `tabHisabi Bucket Template` t
            INNER JOIN `tabHisabi Bucket Template Item` i ON i.parent = t.name
            WHERE t.wallet_id = %(wallet_id)s
              AND t.is_deleted = 0
              AND t.is_active = 1
              AND i.bucket_id = %(bucket_id)s
            ORDER BY t.modified DESC
            """,
            {"wallet_id": self.wallet_id, "bucket_id": self.name},
            as_dict=True,
        )
        if not rows:
            return

        template_titles = ", ".join((row.get("title") or row.get("name") or "").strip() for row in rows[:3])
        action = _("archive") if is_archiving else _("delete")
        frappe.throw(
            _(
                "Cannot {0} this bucket because it is used by active template(s): {1}. "
                "Edit or deactivate those template(s) first."
            ).format(action, template_titles),
            frappe.ValidationError,
        )
