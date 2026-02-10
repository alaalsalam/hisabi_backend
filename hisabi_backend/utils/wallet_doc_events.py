"""Document event hooks for wallet scoping enforcement."""

from __future__ import annotations

import frappe
from frappe import _

from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


WALLET_SCOPED_DOCTYPES = [
    "Hisabi Account",
    "Hisabi Category",
    "Hisabi Transaction",
    "Hisabi Bucket",
    "Hisabi Bucket Template",
    "Hisabi Allocation Rule",
    "Hisabi Allocation Rule Line",
    "Hisabi Transaction Allocation",
    "Hisabi Transaction Bucket",
    "Hisabi Transaction Bucket Expense",
    "Hisabi Budget",
    "Hisabi Goal",
    "Hisabi Debt",
    "Hisabi Debt Installment",
    "Hisabi Debt Request",
    "Hisabi Jameya",
    "Hisabi Jameya Payment",
    "Hisabi FX Rate",
    "Hisabi Custom Currency",
    "Hisabi Attachment",
    "Hisabi Audit Log",
]


def validate_wallet_scope(doc, method=None) -> None:
    """Enforce wallet_id presence + membership for wallet-scoped doctypes."""
    if doc.doctype not in WALLET_SCOPED_DOCTYPES:
        return

    user = frappe.session.user
    if not user or user == "Guest":
        return
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return

    if not hasattr(doc, "wallet_id"):
        return

    if not doc.wallet_id:
        frappe.throw(_("wallet_id is required"), frappe.ValidationError)

    wallet_id = validate_client_id(doc.wallet_id)
    # Viewer is read-only; require member for any mutation.
    require_wallet_member(wallet_id, user, min_role="member")
