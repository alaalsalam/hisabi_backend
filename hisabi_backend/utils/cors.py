"""No-op CORS helpers.

Frappe's site_config allow_cors is the single source of CORS headers.
These functions remain as safe stubs to avoid any accidental duplicate headers.
"""

from __future__ import annotations

from typing import Optional


def _get_allowed_origin(origin: Optional[str]) -> Optional[str]:
    return origin


def _apply_headers(response, _origin: Optional[str]):
    return response


def handle_preflight():
    return None


def add_cors_headers(response):
    return response
