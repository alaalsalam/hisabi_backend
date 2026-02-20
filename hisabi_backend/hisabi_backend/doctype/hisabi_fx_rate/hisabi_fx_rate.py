"""DocType controller for Hisabi FX Rate."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe.model.document import Document

from hisabi_backend.utils.fx_defaults import parse_enabled_currencies, seed_wallet_default_fx_rates
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


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


@frappe.whitelist()
def seed_default_rates_for_wallet(
    wallet_id: str,
    base_currency: str | None = None,
    enabled_currencies: str | None = None,
    overwrite_defaults: int = 0,
) -> dict:
    """Desk helper: seed default FX matrix for a wallet into Hisabi FX Rate rows."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.PermissionError)

    wallet_id = validate_client_id(wallet_id)
    if not (frappe.has_role("System Manager") or frappe.has_role("Hisabi Admin")):
        require_wallet_member(wallet_id, user, min_role="member")

    base = str(base_currency or "").strip().upper() if base_currency else None
    enabled = parse_enabled_currencies(enabled_currencies)
    summary = seed_wallet_default_fx_rates(
        wallet_id=wallet_id,
        user=user,
        base_currency=base,
        enabled_currencies=enabled,
        overwrite_defaults=overwrite_defaults,
    )
    return {"ok": True, "wallet_id": wallet_id, "seed": summary}
