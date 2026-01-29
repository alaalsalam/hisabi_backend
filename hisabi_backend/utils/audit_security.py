"""Security audit events written to Hisabi Audit Log."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import frappe
from frappe.utils import now_datetime

from hisabi_backend.utils.request_context import get_request_ip, get_user_agent


def audit_security_event(
	event_type: str,
	*,
	user: Optional[str] = None,
	device_id: Optional[str] = None,
	related_entity_type: Optional[str] = None,
	related_entity_id: Optional[str] = None,
	payload: Optional[Dict[str, Any]] = None,
) -> None:
	"""Best-effort append-only audit log entry."""
	try:
		doc = frappe.new_doc("Hisabi Audit Log")
		real_user = user or (frappe.session.user if frappe.session.user else None) or None
		doc.user = real_user or "Guest"
		# Security events must still be wallet-scoped in our schema; pick best-available wallet_id.
		wallet_id = None
		if payload:
			wallet_id = payload.get("wallet_id")
		if not wallet_id and real_user and real_user != "Guest":
			wallet_id = frappe.get_value("Hisabi User", {"user": real_user}, "default_wallet")
		if not wallet_id and real_user and real_user != "Guest":
			wallet_id = frappe.get_value("Hisabi Wallet Member", {"user": real_user, "status": "active"}, "wallet")
		if hasattr(doc, "wallet_id"):
			if not wallet_id:
				# Cannot write because schema requires wallet_id.
				return
			doc.wallet_id = wallet_id  # type: ignore[assignment]

		doc.status = "security_event" if "security_event" in (doc.meta.get_field("status").options or "") else "accepted"
		if hasattr(doc, "event_type"):
			doc.event_type = event_type  # type: ignore[attr-defined]
		doc.device_id = device_id
		if hasattr(doc, "ip"):
			doc.ip = get_request_ip()  # type: ignore[attr-defined]
		if hasattr(doc, "user_agent"):
			doc.user_agent = get_user_agent()  # type: ignore[attr-defined]
		if hasattr(doc, "related_entity_type"):
			doc.related_entity_type = related_entity_type  # type: ignore[attr-defined]
		if hasattr(doc, "related_entity_id"):
			doc.related_entity_id = related_entity_id  # type: ignore[attr-defined]

		body = payload or {}
		body.setdefault("server_time", now_datetime().isoformat())
		doc.payload_json = json.dumps(body, ensure_ascii=False)
		doc.insert(ignore_permissions=True)
	except Exception:
		# Never block user action on audit log failures.
		frappe.log_error("Failed to write security audit log")
