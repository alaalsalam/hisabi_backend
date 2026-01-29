from __future__ import annotations

import frappe


def execute() -> None:
	"""Remove legacy User custom fields added by Hisabi (now moved to Hisabi User)."""
	fieldnames = [
		"phone",
		"custom_phone_verified",
		"custom_default_wallet",
		"custom_locale",
		"custom_name_ar",
		"custom_phone",
	]
	for fieldname in fieldnames:
		name = frappe.get_value("Custom Field", {"dt": "User", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)
