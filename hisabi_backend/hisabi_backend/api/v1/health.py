"""Healthcheck endpoints."""

import frappe
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
def ping() -> dict:
    return {
        "status": "ok",
        "server_time": now_datetime().isoformat(),
        "version": "v1",
        "app": "hisabi_backend",
    }
