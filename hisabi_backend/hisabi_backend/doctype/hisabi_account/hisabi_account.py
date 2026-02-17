"""DocType controller for Hisabi Account."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class HisabiAccount(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        self._normalize_currencies()
        self._validate_parent_account()
        self._validate_multi_currency_shape()
        self._validate_base_currency_mutation()

    def _normalize_currencies(self) -> None:
        if self.currency:
            self.currency = str(self.currency).strip().upper()
        if self.base_currency:
            self.base_currency = str(self.base_currency).strip().upper()
        if not self.base_currency and self.currency:
            self.base_currency = self.currency

    def _validate_parent_account(self) -> None:
        if not self.parent_account:
            return
        if self.parent_account == self.name:
            frappe.throw(_("Account cannot be its own parent"), frappe.ValidationError)

        parent = frappe.get_doc("Hisabi Account", self.parent_account)
        if parent.wallet_id != self.wallet_id:
            frappe.throw(_("Parent account must be in the same wallet"), frappe.PermissionError)
        if cint(parent.is_deleted):
            frappe.throw(_("Parent account is deleted"), frappe.ValidationError)

        self.is_multi_currency = 0
        self.group_id = self.group_id or parent.group_id or parent.client_id or parent.name
        self.base_currency = parent.base_currency or parent.currency or self.base_currency

    def _validate_multi_currency_shape(self) -> None:
        if cint(self.is_multi_currency):
            if self.parent_account:
                frappe.throw(_("Multi-currency parent account cannot have parent_account"), frappe.ValidationError)
            if not self.base_currency:
                frappe.throw(_("base_currency is required for multi-currency account"), frappe.ValidationError)
            if not self.currency:
                self.currency = self.base_currency
            if not self.group_id:
                self.group_id = self.client_id or self.name
        elif not self.base_currency and self.currency:
            self.base_currency = self.currency

    def _validate_base_currency_mutation(self) -> None:
        if self.is_new():
            return
        previous = self.get_doc_before_save()
        if not previous:
            return

        prev_base = str(previous.base_currency or previous.currency or "").strip().upper()
        next_base = str(self.base_currency or self.currency or "").strip().upper()
        if not prev_base or prev_base == next_base:
            return
        if self._can_change_base_currency():
            return

        frappe.throw(
            _("Base currency can only be changed when account balance is zero"),
            frappe.ValidationError,
        )

    def _can_change_base_currency(self) -> bool:
        if abs(flt(self.current_balance or 0)) > 0.000001:
            return False
        if abs(flt(self.opening_balance or 0)) > 0.000001:
            return False

        if not cint(self.is_multi_currency):
            return True

        child_rows = frappe.get_all(
            "Hisabi Account",
            filters={
                "wallet_id": self.wallet_id,
                "group_id": self.group_id or self.client_id or self.name,
                "parent_account": self.name,
                "is_deleted": 0,
            },
            fields=["current_balance", "opening_balance"],
        )
        for row in child_rows:
            if abs(flt(row.get("current_balance") or 0)) > 0.000001:
                return False
            if abs(flt(row.get("opening_balance") or 0)) > 0.000001:
                return False
        return True
