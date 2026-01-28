"""User validation hooks for Hisabi auth v2."""

from __future__ import annotations

import re

import frappe
from frappe import _


_PHONE_DIGITS_RE = re.compile(r"\\d+")


def normalize_custom_phone(phone: str) -> str:
	phone = (phone or "").strip()
	if not phone:
		return ""
	if phone.startswith("00"):
		phone = "+" + phone[2:]
	digits = "".join(_PHONE_DIGITS_RE.findall(phone))
	if not digits:
		frappe.throw(_("Invalid phone"), frappe.ValidationError)
	if len(digits) < 8 or len(digits) > 15:
		frappe.throw(_("Invalid phone length"), frappe.ValidationError)
	return "+" + digits


def validate_user_phone(doc, _method=None) -> None:
	"""Normalize and enforce uniqueness for User.custom_phone."""
	if not hasattr(doc, "custom_phone"):
		return

	if doc.custom_phone:
		doc.custom_phone = normalize_custom_phone(doc.custom_phone)

	# Best-effort uniqueness check; the DB unique constraint may not exist on all deployments.
	if doc.custom_phone:
		other = frappe.db.get_value(
			"User",
			{"custom_phone": doc.custom_phone, "name": ["!=", doc.name]},
			"name",
		)
		if other:
			frappe.throw(_("Phone already registered"), frappe.ValidationError)

