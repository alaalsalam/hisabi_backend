"""DocType controller for Hisabi FX Rate."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe.model.document import Document


class HisabiFXRate(Document):
    def validate(self):
        self.base_currency = str(self.base_currency or "").strip().upper()
        self.quote_currency = str(self.quote_currency or "").strip().upper()
        self.source = str(self.source or "custom").strip().lower()
        self.last_updated = self.last_updated or now_datetime()
        self.effective_date = self.effective_date or now_datetime()

        if not self.base_currency or not self.quote_currency:
            frappe.throw(_("base_currency and quote_currency are required"), frappe.ValidationError)
        if self.base_currency == self.quote_currency:
            frappe.throw(_("base_currency and quote_currency must be different"), frappe.ValidationError)
        if flt(self.rate or 0) <= 0:
            frappe.throw(_("rate must be greater than zero"), frappe.ValidationError)

        allowed_sources = {"default", "custom", "api"}
        if self.source not in allowed_sources:
            frappe.throw(_("source must be one of: default, custom, api"), frappe.ValidationError)

    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
