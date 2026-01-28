"""Request context helpers (IP/User-Agent extraction)."""

from __future__ import annotations

import frappe


def get_request_ip() -> str | None:
	req = getattr(frappe.local, "request", None)
	if not req:
		return None
	# Werkzeug request has remote_addr; our tests often use simple dict.
	return getattr(req, "remote_addr", None) or getattr(req, "ip", None) or None


def get_user_agent() -> str | None:
	try:
		return frappe.get_request_header("User-Agent")
	except Exception:
		req = getattr(frappe.local, "request", None)
		if not req:
			return None
		headers = getattr(req, "headers", None) or {}
		return headers.get("User-Agent")

