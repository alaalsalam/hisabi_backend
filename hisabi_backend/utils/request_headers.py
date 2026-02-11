from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import frappe


def strip_expect_header() -> bool:
    """Best-effort removal of `Expect` from request headers/environ.

    This prevents upstream/proxy quirks from surfacing as HTTP 417 in app-level auth handlers.
    """

    removed = False
    request: Any = getattr(frappe.local, "request", None)
    if not request:
        return False

    headers = getattr(request, "headers", None)
    if isinstance(headers, MutableMapping):
        for key in list(headers.keys()):
            if str(key).lower() != "expect":
                continue
            headers.pop(key, None)
            removed = True

    environ = getattr(request, "environ", None)
    if isinstance(environ, MutableMapping) and "HTTP_EXPECT" in environ:
        environ.pop("HTTP_EXPECT", None)
        removed = True

    return removed
