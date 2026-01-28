"""DocType controller for Hisabi Debt Installment."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HisabiDebtInstallment(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("amount must be greater than 0"), frappe.ValidationError)

        if not self.debt:
            return

        debt = frappe.get_doc("Hisabi Debt", self.debt)
        if debt.user != self.user:
            frappe.throw(_("Debt does not belong to user"), frappe.PermissionError)

        total_amount = frappe.db.sql(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM `tabHisabi Debt Installment`
            WHERE debt=%s AND is_deleted=0 AND name != %s
            """,
            (self.debt, self.name),
        )[0][0]
        total_amount = flt(total_amount) + flt(self.amount)

        if total_amount > flt(debt.principal_amount):
            frappe.throw(_("Installments total exceeds principal_amount"), frappe.ValidationError)
