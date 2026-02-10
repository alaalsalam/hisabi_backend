"""DocType controller for Hisabi Recurring Instance."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class HisabiRecurringInstance(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if not self.client_id:
            self.client_id = f"rinst-{frappe.generate_hash(length=12)}"
        self.name = self.client_id
        self.flags.name_set = True

    def validate(self):
        if not self.client_id:
            self.client_id = f"rinst-{frappe.generate_hash(length=12)}"

        if not self.wallet_id:
            frappe.throw(_("wallet_id is required"), frappe.ValidationError)
        if not self.rule_id:
            frappe.throw(_("rule_id is required"), frappe.ValidationError)
        if not self.occurrence_date:
            frappe.throw(_("occurrence_date is required"), frappe.ValidationError)

        self.status = (self.status or "generated").strip().lower()
        if self.status not in {"scheduled", "generated", "skipped"}:
            frappe.throw(_("status is invalid"), frappe.ValidationError)

        existing = frappe.get_value(
            "Hisabi Recurring Instance",
            {
                "wallet_id": self.wallet_id,
                "rule_id": self.rule_id,
                "occurrence_date": self.occurrence_date,
                "is_deleted": 0,
                "name": ["!=", self.name],
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("Recurring instance already exists for this occurrence date"),
                frappe.ValidationError,
            )

        rule_wallet_id = frappe.get_value("Hisabi Recurring Rule", self.rule_id, "wallet_id")
        if not rule_wallet_id:
            frappe.throw(_("rule_id is invalid"), frappe.ValidationError)
        if rule_wallet_id != self.wallet_id:
            frappe.throw(_("rule_id is not in this wallet"), frappe.PermissionError)

        if self.transaction_id:
            tx_wallet_id = frappe.get_value("Hisabi Transaction", self.transaction_id, "wallet_id")
            if tx_wallet_id and tx_wallet_id != self.wallet_id:
                frappe.throw(_("transaction_id is not in this wallet"), frappe.PermissionError)
