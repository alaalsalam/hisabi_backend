"""Healthcheck endpoints."""

from __future__ import annotations

import importlib
from pathlib import Path
import os
import re
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
    env_commit = os.environ.get("HISABI_BACKEND_COMMIT") or os.environ.get("GIT_COMMIT")
    if env_commit:
        return env_commit.strip()[:12] or None

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
    # Runtime source of truth for this app package version.
    # Bench path can expose multiple `hisabi_backend` modules; prefer app root file.
    try:
        root_init = Path(__file__).resolve().parents[3] / "__init__.py"
        if root_init.exists():
            match = re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", root_init.read_text())
            if match:
                return match.group(1)
    except Exception:
        pass

    try:
        app_module = importlib.import_module("hisabi_backend")
        version = getattr(app_module, "__version__", None)
        if version:
            return str(version)
    except Exception:
        pass
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
