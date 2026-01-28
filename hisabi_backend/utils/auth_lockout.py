"""Account lockout helper for failed login protection."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from hisabi_backend.utils.audit_security import audit_security_event


MAX_FAILURES = 5
LOCK_MINUTES = 15


def is_locked(user: str) -> bool:
	locked_until = frappe.get_value("User", user, "account_locked_until")
	return bool(locked_until and locked_until > now_datetime())


def on_login_success(user: str, *, device_id: str | None = None) -> None:
	# Reset counters.
	frappe.db.set_value(
		"User",
		user,
		{
			"failed_login_count": 0,
			"last_failed_login_at": None,
			"account_locked_until": None,
		},
		update_modified=False,
	)
	audit_security_event("login_success", user=user, device_id=device_id)


def on_login_failed(identifier: str, *, user: str | None = None, device_id: str | None = None) -> None:
	# We may not know user if identifier does not exist.
	if user:
		count = frappe.get_value("User", user, "failed_login_count") or 0
		count = int(count) + 1
		values = {"failed_login_count": count, "last_failed_login_at": now_datetime()}
		if count >= MAX_FAILURES:
			lock_until = add_to_date(now_datetime(), minutes=LOCK_MINUTES)
			values["account_locked_until"] = lock_until
			audit_security_event("account_locked", user=user, device_id=device_id, payload={"identifier": identifier})
		frappe.db.set_value("User", user, values, update_modified=False)

	audit_security_event("login_failed", user=user, device_id=device_id, payload={"identifier": identifier})


def raise_if_locked(user: str) -> None:
	if is_locked(user):
		audit_security_event("account_locked", user=user, payload={"reason": "locked"})
		frappe.throw(_("account_locked"), frappe.AuthenticationError)

