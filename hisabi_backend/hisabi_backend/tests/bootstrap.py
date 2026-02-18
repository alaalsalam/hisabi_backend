"""Test harness bootstrap utilities for hisabi backend tests."""

from __future__ import annotations

import os

import frappe
from cryptography.fernet import Fernet
from frappe.utils import encode

ALL_DEPARTMENTS = "All Departments"


def _is_test_context() -> bool:
	"""Return True only for test execution contexts."""
	return bool(getattr(frappe.flags, "in_test", False) or os.environ.get("FRAPPE_ENV") == "test")


def ensure_all_departments() -> None:
	"""Ensure the root Department exists for test record creation."""
	if not _is_test_context():
		return

	if not frappe.db.exists("DocType", "Department"):
		return

	if frappe.db.exists("Department", ALL_DEPARTMENTS):
		return

	department = frappe.get_doc(
		{
			"doctype": "Department",
			"name": ALL_DEPARTMENTS,
			"department_name": ALL_DEPARTMENTS,
			"is_group": 1,
			"parent_department": "",
		}
	)
	department.insert(
		ignore_permissions=True,
		ignore_mandatory=True,
		set_name=ALL_DEPARTMENTS,
	)


def _is_valid_fernet_key(key: str | None) -> bool:
	if not key:
		return False

	try:
		Fernet(encode(key))
		return True
	except Exception:
		return False


def ensure_test_encryption_key() -> None:
	"""Patch invalid encryption key in-memory only during tests."""
	if not _is_test_context():
		return

	current_key = frappe.local.conf.get("encryption_key")
	if _is_valid_fernet_key(current_key):
		return

	frappe.local.conf.encryption_key = Fernet.generate_key().decode()


def run_test_bootstrap() -> None:
	"""Run bootstrap tasks required only while tests are running."""
	if not _is_test_context():
		return

	ensure_test_encryption_key()
	ensure_all_departments()
