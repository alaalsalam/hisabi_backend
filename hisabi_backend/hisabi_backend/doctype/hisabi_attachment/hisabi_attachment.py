"""DocType controller for Hisabi Attachment."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class HisabiAttachment(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if not self.owner_entity_type or not self.owner_client_id:
            frappe.throw(_("owner_entity_type and owner_client_id are required"), frappe.ValidationError)

        if self.owner_entity_type == "Hisabi Transaction":
            tx_wallet = frappe.get_value("Hisabi Transaction", self.owner_client_id, "wallet_id")
            if not tx_wallet:
                frappe.throw(_("Transaction not found"), frappe.ValidationError)
            if self.wallet_id and tx_wallet != self.wallet_id:
                frappe.throw(_("Transaction is not in this wallet"), frappe.PermissionError)
