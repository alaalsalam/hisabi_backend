"""User validation hooks for Hisabi auth v2."""

from __future__ import annotations

import re

import frappe
from frappe import _


_PHONE_DIGITS_RE = re.compile(r"\d+")


def normalize_phone_digits(phone: str, *, strict: bool = False) -> str:
	phone = (phone or "").strip()
	if not phone:
		return ""
	digits = "".join(_PHONE_DIGITS_RE.findall(phone))
	if not digits:
		if strict:
			frappe.throw(_("Invalid phone"), frappe.ValidationError)
		return ""
	if len(digits) < 8 or len(digits) > 15:
		frappe.throw(_("Invalid phone length"), frappe.ValidationError)
	return digits


def validate_user_phone(doc, _method=None) -> None:
	"""Normalize and enforce uniqueness for User.phone."""
	if not hasattr(doc, "phone"):
		return

	if doc.phone:
		doc.phone = normalize_phone_digits(doc.phone, strict=False)

	# Best-effort uniqueness check; the DB unique constraint may not exist on all deployments.
	if doc.phone:
		other = frappe.db.get_value(
			"User",
			{"phone": doc.phone, "name": ["!=", doc.name]},
			"name",
		)
		if other:
			frappe.throw(_("Phone already registered"), frappe.ValidationError)
