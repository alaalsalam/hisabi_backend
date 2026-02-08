"""Healthcheck endpoints."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess

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


def _resolve_git_commit() -> str | None:
    try:
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / ".git").exists():
                commit = subprocess.check_output(
                    ["git", "-C", str(parent), "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=1.0,
                ).strip()
                return commit or None
    except Exception:
        return None
    return None


def _resolve_app_version() -> str | None:
    try:
        return version("hisabi_backend")
    except PackageNotFoundError:
        return None


@frappe.whitelist(allow_guest=True)
def diag() -> dict:
    conf = getattr(frappe.local, "conf", {}) or {}
    # Safety-critical: expose only a boolean for encryption key presence; never return config values.
    return {
        "status": "ok",
        "server_time": now_datetime().isoformat(),
        "site": getattr(frappe.local, "site", None),
        "encryption_key_present": bool(conf.get("encryption_key")),
        "app": {
            "name": "hisabi_backend",
            "version": _resolve_app_version(),
            "commit": _resolve_git_commit(),
        },
    }
