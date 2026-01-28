"""DocType controller for Hisabi Jameya."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, flt, get_datetime


class HisabiJameya(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if flt(self.monthly_amount) <= 0:
            frappe.throw(_("monthly_amount must be greater than 0"), frappe.ValidationError)
        if int(self.total_members or 0) <= 0:
            frappe.throw(_("total_members must be greater than 0"), frappe.ValidationError)
        if int(self.my_turn or 0) < 1 or int(self.my_turn or 0) > int(self.total_members or 0):
            frappe.throw(_("my_turn must be between 1 and total_members"), frappe.ValidationError)
        if self.period and self.period not in {"weekly", "monthly"}:
            frappe.throw(_("Invalid period"), frappe.ValidationError)
        if not self.start_date:
            frappe.throw(_("start_date is required"), frappe.ValidationError)

        self.total_amount = flt(self.monthly_amount) * int(self.total_members or 0)
        if not self.status:
            self.status = "active"

    def after_insert(self):
        self._ensure_schedule()

    def on_update(self):
        if not frappe.db.exists("Hisabi Jameya Payment", {"jameya": self.name, "is_deleted": 0}):
            self._ensure_schedule()

    def _ensure_schedule(self) -> None:
        start_date = get_datetime(self.start_date)
        if not start_date:
            return

        for period_number in range(1, int(self.total_members) + 1):
            if self.period == "weekly":
                due_date = add_days(start_date, 7 * (period_number - 1))
            else:
                due_date = add_months(start_date, period_number - 1)

            client_id = f"{self.client_id}:{period_number}"
            if frappe.db.exists("Hisabi Jameya Payment", {"client_id": client_id, "jameya": self.name}):
                continue

            payment = frappe.new_doc("Hisabi Jameya Payment")
            payment.user = self.user
            payment.client_id = client_id
            payment.name = client_id
            payment.jameya = self.name
            payment.period_number = period_number
            payment.due_date = due_date
            payment.amount = self.monthly_amount
            payment.is_my_turn = 1 if period_number == int(self.my_turn) else 0
            payment.status = "due"
            payment.insert(ignore_permissions=True)
