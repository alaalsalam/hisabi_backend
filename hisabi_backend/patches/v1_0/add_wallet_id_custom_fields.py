from __future__ import annotations

import frappe


def _ensure_custom_field(doctype: str, fieldname: str, fieldtype: str, label: str, options: str | None = None) -> None:
    if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
        return

    cf = frappe.new_doc("Custom Field")
    cf.dt = doctype
    cf.fieldname = fieldname
    cf.fieldtype = fieldtype
    cf.label = label
    if options:
        cf.options = options
    cf.insert(ignore_permissions=True)


def execute() -> None:
    """Add wallet_id link field to all Hisabi doctypes (server authoritative scoping)."""
    doctypes = [
        "Hisabi Account",
        "Hisabi Category",
        "Hisabi Transaction",
        "Hisabi Bucket",
        "Hisabi Allocation Rule",
        "Hisabi Allocation Rule Line",
        "Hisabi Transaction Allocation",
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
        # Optional: device/settings may exist in some deployments
        "Hisabi Settings",
        "Hisabi Device",
    ]

    for dt in doctypes:
        if not frappe.db.exists("DocType", dt):
            continue
        _ensure_custom_field(dt, "wallet_id", "Link", "Wallet", "Hisabi Wallet")

