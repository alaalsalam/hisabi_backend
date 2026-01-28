"""DocType controller for Hisabi Budget."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime

from hisabi_backend.utils.validators import validate_currency


class HisabiBudget(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if self.client_id and not self.name:
            self.name = self.client_id

    def validate(self):
        if self.amount is not None and flt(self.amount) <= 0:
            frappe.throw(_("amount must be greater than 0"), frappe.ValidationError)

        if self.scope_type not in {"total", "category"}:
            frappe.throw(_("Invalid scope_type"), frappe.ValidationError)

        if self.scope_type == "category" and not self.category:
            frappe.throw(_("category is required for category budgets"), frappe.ValidationError)

        if not self.start_date or not self.end_date:
            frappe.throw(_("start_date and end_date are required"), frappe.ValidationError)

        start_dt = get_datetime(self.start_date)
        end_dt = get_datetime(self.end_date)
        if start_dt and end_dt and start_dt > end_dt:
            frappe.throw(_("start_date must be before end_date"), frappe.ValidationError)

        if self.currency:
            self.currency = validate_currency(self.currency, self.user)

        self._validate_overlap(start_dt, end_dt)

    def _validate_overlap(self, start_dt, end_dt) -> None:
        if not start_dt or not end_dt:
            return

        filters = {
            "user": self.user,
            "is_deleted": 0,
            "archived": 0,
            "scope_type": self.scope_type,
        }

        if self.scope_type == "category":
            filters["category"] = self.category
        else:
            filters["category"] = ["is", "not set"]

        existing = frappe.db.get_value(
            "Hisabi Budget",
            {
                **filters,
                "name": ["!=", self.name],
                "start_date": ["<=", end_dt],
                "end_date": [">=", start_dt],
            },
            "name",
        )
        if existing:
            frappe.throw(_("Overlapping budget exists for this period"), frappe.ValidationError)
