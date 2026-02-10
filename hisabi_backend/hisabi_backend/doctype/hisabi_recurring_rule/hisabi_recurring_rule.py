"""DocType controller for Hisabi Recurring Rule."""

from __future__ import annotations

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, now_datetime

from hisabi_backend.utils.validators import validate_currency

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _default_timezone() -> str:
    system_tz = frappe.db.get_single_value("System Settings", "time_zone")
    return (system_tz or "Asia/Aden").strip() or "Asia/Aden"


class HisabiRecurringRule(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if not self.client_id:
            self.client_id = f"rrule-{frappe.generate_hash(length=12)}"
        self.name = self.client_id
        self.flags.name_set = True

    def validate(self):
        if not self.client_id:
            self.client_id = f"rrule-{frappe.generate_hash(length=12)}"

        self.title = (self.title or "").strip()
        if not self.title:
            frappe.throw(_("title is required"), frappe.ValidationError)

        if not self.wallet_id:
            frappe.throw(_("wallet_id is required"), frappe.ValidationError)

        if not self.start_date:
            frappe.throw(_("start_date is required"), frappe.ValidationError)

        self.transaction_type = (self.transaction_type or "").strip().lower()
        if self.transaction_type not in {"income", "expense", "transfer"}:
            frappe.throw(_("transaction_type is invalid"), frappe.ValidationError)

        self.amount = flt(self.amount, 2)
        if self.amount <= 0:
            frappe.throw(_("amount must be greater than 0"), frappe.ValidationError)

        self.currency = validate_currency((self.currency or "").strip().upper(), self.user)

        self.rrule_type = (self.rrule_type or "").strip().lower()
        if self.rrule_type not in {"daily", "weekly", "monthly"}:
            frappe.throw(_("rrule_type is invalid"), frappe.ValidationError)

        self.interval = cint(self.interval or 1)
        if self.interval < 1:
            frappe.throw(_("interval must be at least 1"), frappe.ValidationError)

        if not self.timezone:
            self.timezone = _default_timezone()

        self.end_mode = (self.end_mode or "none").strip().lower()
        if self.end_mode not in {"none", "until", "count"}:
            frappe.throw(_("end_mode is invalid"), frappe.ValidationError)

        if self.end_mode == "none":
            self.until_date = None
            self.count = None
        elif self.end_mode == "until":
            if not self.until_date:
                frappe.throw(_("until_date is required when end_mode is until"), frappe.ValidationError)
            self.count = None
        elif self.end_mode == "count":
            self.count = cint(self.count or 0)
            if self.count <= 0:
                frappe.throw(_("count must be greater than 0 when end_mode is count"), frappe.ValidationError)
            self.until_date = None

        self._normalize_weekly_fields()
        self._normalize_monthly_fields()

        if self.transaction_type == "income":
            self.category_id = None
        if self.transaction_type == "transfer":
            self.category_id = None

    def _normalize_weekly_fields(self) -> None:
        if self.rrule_type != "weekly":
            self.byweekday = None
            return

        raw = self.byweekday
        if isinstance(raw, list):
            values = raw
        else:
            values = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        values = parsed
                except Exception:
                    values = []

        cleaned = []
        seen = set()
        for value in values:
            code = str(value or "").strip().upper()
            if code in WEEKDAY_CODES and code not in seen:
                cleaned.append(code)
                seen.add(code)

        if not cleaned:
            start = get_datetime(self.start_date)
            cleaned = [WEEKDAY_CODES[start.weekday()]]

        self.byweekday = json.dumps(cleaned, separators=(",", ":"))

    def _normalize_monthly_fields(self) -> None:
        if self.rrule_type != "monthly":
            self.bymonthday = None
            return

        month_day = cint(self.bymonthday or 0)
        if month_day <= 0:
            month_day = get_datetime(self.start_date).day
        if month_day < 1 or month_day > 31:
            frappe.throw(_("bymonthday must be between 1 and 31"), frappe.ValidationError)
        self.bymonthday = month_day
