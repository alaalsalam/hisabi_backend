"""Structured API error helpers (single format for clients)."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import frappe


def _get_request_id() -> str:
    req_id = getattr(frappe.local, "request_id", None)
    if not req_id:
        req_id = str(uuid.uuid4())
        frappe.local.request_id = req_id
    return req_id


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    user_message: str,
    action: str,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    request_id = _get_request_id()
    payload = {
        "code": code,
        "message": message,
        "user_message": user_message,
        "action": action,
        "request_id": request_id,
    }
    if extra:
        payload.update(extra)

    frappe.local.response["http_status_code"] = status_code
    frappe.local.response["error"] = payload
    frappe.local.response["message"] = None
    return {"error": payload}
