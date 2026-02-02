"""Helpers for reading params from Frappe RPC requests.

Frappe typically populates `frappe.form_dict` from query string and form posts.
For mobile clients, we also accept JSON request bodies (application/json).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import frappe


def _get_json_body() -> dict[str, Any]:
    req = getattr(frappe.local, "request", None)
    if not req:
        return {}

    # werkzeug Request
    headers = getattr(req, "headers", None) or {}
    content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return {}

    try:
        data = req.get_json(silent=True)
    except Exception:
        data = None
    return data if isinstance(data, dict) else {}


def get_request_param(name: str) -> Any:
    """Read a request param from query string / form_dict, falling back to JSON body."""
    if not name:
        return None
    # Query string and form-encoded POST.
    val = frappe.form_dict.get(name)
    if val is not None and val != "":
        return val
    # Werkzeug request args/form (some /api/method calls don't populate form_dict with query params reliably).
    req = getattr(frappe.local, "request", None)
    if req:
        try:
            val = req.args.get(name)
        except Exception:
            val = None
        if val is not None and val != "":
            return val
        # Some deployments may not populate `request.args` but still have a raw query string.
        try:
            qs = getattr(req, "query_string", None) or b""
            if isinstance(qs, bytes):
                qs = qs.decode("utf-8", errors="ignore")
            parsed = parse_qs(qs, keep_blank_values=False)
            raw = parsed.get(name)
            if raw and raw[0] != "":
                return raw[0]
        except Exception:
            pass
        try:
            val = req.form.get(name)
        except Exception:
            val = None
        if val is not None and val != "":
            return val
    # JSON body (application/json).
    return _get_json_body().get(name)
