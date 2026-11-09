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


def _ensure_json_body_in_form_dict(req) -> None:
    """Re-parse the JSON request body into form_dict when Frappe's make_form_dict() missed it.

    Frappe 16's make_form_dict() only merges the JSON body when request.is_json is True
    (i.e. Content-Type is exactly 'application/json').  Under some proxy configurations
    the mimetype doesn't reach the WSGI layer correctly, so form_dict ends up empty.
    This hook is the last safe place to correct that before the whitelisted function is called.
    """
    if req is None or req.method not in ("POST", "PUT", "PATCH"):
        return
    # If form_dict already has meaningful keys beyond just 'cmd', parsing worked fine.
    extra_keys = set(frappe.form_dict.keys()) - {"cmd"}
    if extra_keys:
        return
    try:
        import orjson

        raw = req.get_data(as_text=True)
        if not raw:
            return
        args = orjson.loads(raw)
        if isinstance(args, dict):
            frappe.form_dict.update(args)
            frappe.form_dict.pop("_", None)
    except Exception:
        pass


def authenticate_request():
    """Authenticate Bearer tokens for Hisabi v1 endpoints (except auth endpoints)."""
    req = getattr(frappe.local, "request", None)
    if req and req.method == "OPTIONS":
        return None

    cmd = frappe.form_dict.get("cmd")
    if not cmd:
        cmd = _extract_method_from_path(getattr(req, "path", None))

    # Realtime (socket.io) auth probe: the Frappe socketio server calls frappe.realtime.get_user_info
    # and forwards the client's Authorization header. Authenticate a Hisabi device token here so the
    # socket resolves to the real user (enabling wallet-scoped realtime events for the standalone
    # SPA, which uses token auth, not session cookies). Best-effort ONLY: never throw — cookie/Desk
    # realtime clients must keep working, and a bad/absent token falls through to Guest handling.
    if cmd == "frappe.realtime.get_user_info":
        auth = frappe.get_request_header("Authorization") or ""
        if auth.lower().startswith("bearer "):
            try:
                require_device_token_auth()
            except Exception:
                pass
        return None

    if not _is_hisabi_v1_cmd(cmd):
        return None
    if cmd in _SKIP_CMDS:
        return None

    try:
        require_device_token_auth()
    except Exception:
        frappe.throw(_("Unauthorized"), frappe.PermissionError)

    # Force-merge the JSON body into form_dict in case Frappe's make_form_dict()
    # didn't parse it (happens when request.is_json is False due to proxy/header issues).
    _ensure_json_body_in_form_dict(req)
    return None
