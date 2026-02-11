"""DocType controller for Hisabi Transaction."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from hisabi_backend.domain.allocation_engine import apply_auto_allocations

class HisabiTransaction(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user

    def validate(self):
        if self.transaction_type == "transfer" and self.account and self.to_account:
            if self.account == self.to_account:
                frappe.throw(_("Transfer account cannot match to_account"))
        self._warn_fx_sanity()

    def _warn_fx_sanity(self):
        if not self.account or not self.currency:
            return
        account_currency = frappe.get_value("Hisabi Account", self.account, "currency")
        if not account_currency:
            return
        tx_currency = str(self.currency or "").strip().upper()
        account_currency = str(account_currency or "").strip().upper()
        if not tx_currency or tx_currency == account_currency:
            return
        try:
            fx_rate = float(self.fx_rate_used or 0)
        except Exception:
            fx_rate = 0.0
        if fx_rate <= 0:
            frappe.msgprint(
                _("FX sanity warning: fx_rate_used should be > 0 when transaction currency differs from account currency."),
                indicator="orange",
                alert=True,
            )

    def after_insert(self):
        apply_auto_allocations(self)

    def on_update(self):
        apply_auto_allocations(self)
