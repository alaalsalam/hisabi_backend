"""DocType controller for Hisabi Settings."""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint, now_datetime
from frappe.model.document import Document


class HisabiSettings(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        self._normalize_defaults()

    def validate(self):
        self._normalize_defaults()

    def _normalize_defaults(self):
        user_doc = None
        if self.user:
            try:
                user_doc = frappe.get_cached_doc("User", self.user)
            except Exception:
                user_doc = None

        if not self.user_name and user_doc:
            self.user_name = (
                getattr(user_doc, "full_name", None)
                or getattr(user_doc, "first_name", None)
                or self.user
            )

        base_currency = str(self.base_currency or "SAR").strip().upper() or "SAR"
        self.base_currency = base_currency

        if not self.enabled_currencies:
            self.enabled_currencies = json.dumps([base_currency], ensure_ascii=False)
        elif isinstance(self.enabled_currencies, list):
            normalized = [str(code or "").strip().upper() for code in self.enabled_currencies if str(code or "").strip()]
            if base_currency not in normalized:
                normalized.insert(0, base_currency)
            self.enabled_currencies = json.dumps(list(dict.fromkeys(normalized)), ensure_ascii=False)

        if not self.locale:
            self.locale = "ar-SA"

        if not self.phone_number and user_doc:
            self.phone_number = (
                getattr(user_doc, "mobile_no", None)
                or getattr(user_doc, "phone", None)
                or getattr(user_doc, "custom_phone", None)
                or ""
            )

        if not self.notifications_preferences:
            self.notifications_preferences = json.dumps([], ensure_ascii=False)

        if self.enforce_fx is None:
            self.enforce_fx = 0
        self.enforce_fx = 1 if cint(self.enforce_fx) else 0

        if self.week_start_day is None or str(self.week_start_day).strip() == "":
            self.week_start_day = 6

        if self.use_arabic_numerals is None:
            self.use_arabic_numerals = 0
        self.use_arabic_numerals = 1 if cint(self.use_arabic_numerals) else 0

        if not getattr(self, "app_language", None):
            locale_prefix = str(self.locale or "").split("-")[0].strip().lower()
            self.app_language = locale_prefix or "ar"

        if not getattr(self, "theme_mode", None):
            self.theme_mode = "system"
        self.theme_mode = str(self.theme_mode).strip().lower() or "system"
        if self.theme_mode not in {"light", "dark", "system"}:
            self.theme_mode = "system"

        if not self.client_id and self.wallet_id:
            self.client_id = f"settings-{self.wallet_id}"

        now_dt = now_datetime()
        if not self.client_created_ms:
            self.client_created_ms = int(now_dt.timestamp() * 1000)
        if not self.client_modified_ms:
            self.client_modified_ms = int(now_dt.timestamp() * 1000)
        if not self.doc_version:
            self.doc_version = 1
        if not self.server_modified:
            self.server_modified = now_dt
