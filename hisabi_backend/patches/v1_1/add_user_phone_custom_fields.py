from __future__ import annotations

import frappe


def _ensure_custom_field(
	doctype: str,
	fieldname: str,
	fieldtype: str,
	label: str,
	*,
	options: str | None = None,
	unique: int | None = None,
	default: str | int | None = None,
) -> None:
	if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
		return

	cf = frappe.new_doc("Custom Field")
	cf.dt = doctype
	cf.fieldname = fieldname
	cf.fieldtype = fieldtype
	cf.label = label
	if options:
		cf.options = options
	if unique is not None:
		cf.unique = unique
	if default is not None:
		cf.default = str(default)
	cf.insert(ignore_permissions=True)


def execute() -> None:
	"""Add Hisabi auth-related custom fields to User.

	We must use Custom Field for core DocTypes like User (cannot ship User JSON in an app).
	"""
	_ensure_custom_field("User", "custom_phone", "Data", "Phone", unique=0)
	_ensure_custom_field("User", "custom_phone_verified", "Check", "Phone Verified", default=0)
	_ensure_custom_field("User", "custom_default_wallet", "Link", "Default Wallet", options="Hisabi Wallet")
	_ensure_custom_field("User", "custom_locale", "Data", "Locale")
	_ensure_custom_field("User", "custom_name_ar", "Data", "Name (AR)")

