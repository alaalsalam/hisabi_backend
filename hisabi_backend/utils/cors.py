from __future__ import annotations

from typing import Optional

import frappe


ALLOWED_ORIGINS = {
    "http://95.111.251.41:8081",
    "http://localhost:5173",
    "http://localhost:8081",
    "https://hisabi.yemenfrappe.com",
}

ALLOWED_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
ALLOWED_HEADERS = "Content-Type, Authorization, X-Frappe-CSRF-Token, X-Requested-With"


def _get_allowed_origin(origin: Optional[str]) -> Optional[str]:
    if not origin:
        return None
    return origin if origin in ALLOWED_ORIGINS else None


def _apply_headers(response, origin: Optional[str]):
    if not origin:
        return response
    # Avoid duplicate headers if another layer already set them.
    if response.headers.get("Access-Control-Allow-Origin"):
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    if not response.headers.get("Access-Control-Allow-Credentials"):
        response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = ALLOWED_METHODS
    response.headers["Access-Control-Allow-Headers"] = ALLOWED_HEADERS
    response.headers["Vary"] = "Origin"
    return response


def handle_preflight():
    req = getattr(frappe.local, "request", None)
    if not req or req.method != "OPTIONS":
        return None
    origin = _get_allowed_origin(req.headers.get("Origin"))
    if not origin:
        return None
    # Frappe short-circuits OPTIONS internally; after_request will add headers.
    return None


def add_cors_headers(response):
    req = getattr(frappe.local, "request", None)
    if not req:
        return response
    origin = _get_allowed_origin(req.headers.get("Origin"))
    _apply_headers(response, origin)
    return response
