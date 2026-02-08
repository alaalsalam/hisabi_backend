from __future__ import annotations

import frappe
from frappe import _

from hisabi_backend.utils.security import require_device_token_auth


_SKIP_CMDS = {
    "hisabi_backend.api.v1.register_user",
    "hisabi_backend.api.v1.login",
    "hisabi_backend.api.v1.logout",
    "hisabi_backend.api.v1.auth.register",
    "hisabi_backend.api.v1.auth.login",
    "hisabi_backend.api.v1.auth.register_device",
    "hisabi_backend.api.v1.auth.link_device_to_user",
    # Health and diag must remain curlable for operations checks without device auth.
    "hisabi_backend.api.v1.health.ping",
    "hisabi_backend.api.v1.health.diag",
}


def _extract_method_from_path(path: str | None) -> str | None:
    if not path:
        return None
    if "/api/method/" not in path:
        return None
    method = path.split("/api/method/", 1)[1]
    if "?" in method:
        method = method.split("?", 1)[0]
    return method or None


def _is_hisabi_v1_cmd(cmd: str | None) -> bool:
    if not cmd:
        return False
    return cmd.startswith("hisabi_backend.api.v1.")


def authenticate_request():
    """Authenticate Bearer tokens for Hisabi v1 endpoints (except auth endpoints)."""
    req = getattr(frappe.local, "request", None)
    if req and req.method == "OPTIONS":
        return None

    cmd = frappe.form_dict.get("cmd")
    if not cmd:
        cmd = _extract_method_from_path(getattr(req, "path", None))
    if not _is_hisabi_v1_cmd(cmd):
        return None
    if cmd in _SKIP_CMDS:
        return None

    try:
        require_device_token_auth()
    except Exception:
        frappe.throw(_("Unauthorized"), frappe.PermissionError)
    return None
