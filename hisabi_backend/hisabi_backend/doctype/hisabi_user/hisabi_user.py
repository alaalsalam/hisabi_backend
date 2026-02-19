from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from hisabi_backend.utils.user_lifecycle import delete_user_account_and_data, set_user_frozen_state


def _require_admin() -> None:
    if frappe.session.user == "Administrator":
        return
    if frappe.has_permission("Hisabi User", ptype="write"):
        return
    frappe.throw(_("Not permitted"), frappe.PermissionError)


class HisabiUser(Document):
    pass


@frappe.whitelist()
def freeze_account(docname: str, reason: str | None = None) -> dict:
    _require_admin()
    doc = frappe.get_doc("Hisabi User", docname)
    result = set_user_frozen_state(
        doc.user,
        freeze=True,
        actor=frappe.session.user,
        reason=reason,
    )
    frappe.db.commit()
    return result


@frappe.whitelist()
def unfreeze_account(docname: str) -> dict:
    _require_admin()
    doc = frappe.get_doc("Hisabi User", docname)
    result = set_user_frozen_state(
        doc.user,
        freeze=False,
        actor=frappe.session.user,
    )
    frappe.db.commit()
    return result


@frappe.whitelist()
def delete_account(docname: str, delete_frappe_user: int = 1) -> dict:
    _require_admin()
    doc = frappe.get_doc("Hisabi User", docname)
    result = delete_user_account_and_data(
        doc.user,
        actor=frappe.session.user,
        delete_frappe_user=bool(int(delete_frappe_user or 0)),
    )
    frappe.db.commit()
    return result
