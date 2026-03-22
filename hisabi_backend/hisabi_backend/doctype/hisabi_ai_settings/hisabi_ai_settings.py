from __future__ import annotations

import json
import os
from pathlib import Path

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


RUNTIME_DIR_NAME = ".hisabi_runtime"
RUNTIME_FILE_NAME = "ai_settings.json"


def _runtime_dir() -> Path:
    return Path(frappe.get_site_path(RUNTIME_DIR_NAME))


def _runtime_file() -> Path:
    return _runtime_dir() / RUNTIME_FILE_NAME


def _clean_text(value: str | None, default: str | None = None) -> str | None:
    cleaned = (value or "").strip()
    if cleaned:
        return cleaned
    return default


class HisabiAISettings(Document):
    def validate(self):
        self.preferred_provider = _clean_text(self.preferred_provider, "openai")
        self.openai_base_url = _clean_text(self.openai_base_url)
        self.openai_text_model = _clean_text(self.openai_text_model, "gpt-5-mini")
        self.openai_audio_model = _clean_text(
            self.openai_audio_model, "gpt-4o-mini-transcribe"
        )
        self.gemini_base_url = _clean_text(
            self.gemini_base_url, "https://generativelanguage.googleapis.com"
        )
        self.gemini_text_model = _clean_text(
            self.gemini_text_model, "gemini-2.5-flash-lite"
        )
        self.runtime_source = "frappe-site-settings"
        self.runtime_file_path = str(_runtime_file())

        if self.preferred_provider not in {"openai", "gemini"}:
            self.preferred_provider = "openai"

        if int(self.enable_ai or 0):
            openai_key = self.get_password("openai_api_key", raise_exception=False) or ""
            gemini_key = self.get_password("gemini_api_key", raise_exception=False) or ""
            if not openai_key.strip() and not gemini_key.strip():
                frappe.throw("أدخل مفتاحًا صالحًا لموفر واحد على الأقل عند تفعيل الذكاء الاصطناعي.")

    def on_update(self):
        runtime_dir = _runtime_dir()
        runtime_file = _runtime_file()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(runtime_dir, 0o700)

        payload = {
            "source": "frappe-site-settings",
            "updated_at": now_datetime().isoformat(),
            "enabled": bool(int(self.enable_ai or 0)),
            "preferred_provider": self.preferred_provider or "openai",
            "local_fallback_enabled": bool(int(self.enable_local_fallback or 0)),
            "openai": {
                "enabled": bool(int(self.enable_ai or 0)),
                "api_key": self.get_password("openai_api_key", raise_exception=False) or "",
                "base_url": self.openai_base_url or "",
                "text_model": self.openai_text_model or "gpt-5-mini",
                "audio_model": self.openai_audio_model or "gpt-4o-mini-transcribe",
            },
            "gemini": {
                "enabled": bool(int(self.enable_ai or 0)),
                "api_key": self.get_password("gemini_api_key", raise_exception=False) or "",
                "base_url": self.gemini_base_url or "https://generativelanguage.googleapis.com",
                "text_model": self.gemini_text_model or "gemini-2.5-flash-lite",
            },
        }

        temp_file = runtime_dir / f"{RUNTIME_FILE_NAME}.tmp"
        temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp_file, 0o600)
        temp_file.replace(runtime_file)
        os.chmod(runtime_file, 0o600)

        self.db_set("last_runtime_sync", now_datetime(), update_modified=False)
        self.db_set("runtime_source", "frappe-site-settings", update_modified=False)
        self.db_set("runtime_file_path", str(runtime_file), update_modified=False)
