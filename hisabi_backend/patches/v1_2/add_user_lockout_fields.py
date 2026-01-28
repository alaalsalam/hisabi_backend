from __future__ import annotations

import frappe


def _ensure_custom_field(
	doctype: str,
	fieldname: str,
	fieldtype: str,
	label: str,
	*,
	default: str | int | None = None,
) -> None:
	if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
		return

	cf = frappe.new_doc("Custom Field")
	cf.dt = doctype
	cf.fieldname = fieldname
	cf.fieldtype = fieldtype
	cf.label = label
	if default is not None:
		cf.default = str(default)
	cf.insert(ignore_permissions=True)


def execute() -> None:
	"""Add lockout fields to User for auth hardening."""
	_ensure_custom_field("User", "failed_login_count", "Int", "Failed Login Count", default=0)
	_ensure_custom_field("User", "last_failed_login_at", "Datetime", "Last Failed Login At")
	_ensure_custom_field("User", "account_locked_until", "Datetime", "Account Locked Until")

