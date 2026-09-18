"""Tests for the upgraded Smart Input: voice transcription + stronger text parsing (2026-07-04)."""

import base64
import json

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase

from hisabi_backend.install import ensure_roles
from hisabi_backend.hisabi_backend.tests.test_ai_advisor import (
    _make_authed_user,
    _MockAI,
    _MockAISettings,
)


class TestTranscribeAudio(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("ai_voice")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_transcribe_happy_path(self):
        import hisabi_backend.api.v1.ai_advisor as mod
        from hisabi_backend.api.v1.ai_advisor import transcribe_audio

        orig = mod._transcribe_openai
        mod._transcribe_openai = lambda audio, mime, settings: "قهوة بخمسة ريال"
        try:
            with _MockAISettings(enable=True, key="sk-x", provider="openai"):
                r = transcribe_audio(
                    audio_base64=base64.b64encode(b"fake-audio-bytes").decode(),
                    wallet_id=self.wallet_id, mime_type="audio/webm", device_id=self.device_id,
                )
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["transcript"], "قهوة بخمسة ريال")
        finally:
            mod._transcribe_openai = orig

    def test_transcribe_data_url_prefix_tolerated(self):
        import hisabi_backend.api.v1.ai_advisor as mod
        from hisabi_backend.api.v1.ai_advisor import transcribe_audio

        orig = mod._transcribe_openai
        mod._transcribe_openai = lambda audio, mime, settings: "نص"
        try:
            b64 = base64.b64encode(b"abc").decode()
            with _MockAISettings(enable=True, key="sk-x", provider="openai"):
                r = transcribe_audio(
                    audio_base64=f"data:audio/webm;base64,{b64}",
                    wallet_id=self.wallet_id, device_id=self.device_id,
                )
            self.assertEqual(r["status"], "ok")
        finally:
            mod._transcribe_openai = orig

    def test_transcribe_empty_audio_raises(self):
        from hisabi_backend.api.v1.ai_advisor import transcribe_audio

        with _MockAISettings(enable=True, key="sk-x"):
            with self.assertRaises(frappe.ValidationError):
                transcribe_audio(audio_base64="", wallet_id=self.wallet_id, device_id=self.device_id)

    def test_transcribe_ai_disabled_raises(self):
        from hisabi_backend.api.v1.ai_advisor import transcribe_audio

        with _MockAISettings(enable=False):
            with self.assertRaises(frappe.ValidationError):
                transcribe_audio(
                    audio_base64=base64.b64encode(b"x").decode(),
                    wallet_id=self.wallet_id, device_id=self.device_id,
                )

    def test_transcribe_cross_wallet_denied(self):
        from hisabi_backend.api.v1.ai_advisor import transcribe_audio

        _o, _d, other_wallet, _t = _make_authed_user("ai_voice_cross")
        frappe.set_user(self.user.name)
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.token}"}}
        )()
        with _MockAISettings(enable=True, key="sk-x"):
            with self.assertRaises(frappe.PermissionError):
                transcribe_audio(
                    audio_base64=base64.b64encode(b"x").decode(),
                    wallet_id=other_wallet, device_id=self.device_id,
                )


class TestParseTransactionsMapping(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("ai_parse_map")
        frappe.get_doc({
            "doctype": "Hisabi Category", "client_id": f"c-{frappe.generate_hash(6)}",
            "category_name": "طعام", "kind": "expense",
            "user": self.user.name, "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_maps_real_category_and_normalizes(self):
        from hisabi_backend.api.v1.ai_advisor import parse_transactions

        mock = json.dumps({"transactions": [
            {"amount": "25", "currency": "sar", "transaction_type": "EXPENSE",
             "description": "غداء", "date": None, "category": "طعام", "account": None, "confidence": 1.5},
            {"amount": 0, "transaction_type": "expense", "description": "صفر يُتجاهل"},
            {"amount": 10, "transaction_type": "weird", "category": "لا-توجد", "description": "نوع خاطئ"},
        ]})
        with _MockAISettings(enable=True), _MockAI(mock):
            r = parse_transactions(
                text="غداء 25 ريال", wallet_id=self.wallet_id,
                default_currency="SAR", device_id=self.device_id,
            )
        self.assertEqual(r["status"], "ok")
        txns = r["transactions"]
        self.assertEqual(len(txns), 2, "zero-amount row must be dropped")
        self.assertEqual(txns[0]["amount"], 25.0)
        self.assertEqual(txns[0]["currency"], "SAR")
        self.assertEqual(txns[0]["transaction_type"], "expense")
        self.assertEqual(txns[0]["category"], "طعام", "must map to the real wallet category")
        self.assertLessEqual(txns[0]["confidence"], 1.0, "confidence must be clamped to <=1")
        self.assertEqual(txns[1]["transaction_type"], "expense", "invalid type coerced to expense")
        self.assertIsNone(txns[1]["category"], "non-matching category must be dropped (never invented)")


class TestParseReceiptImage(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("ai_receipt")
        frappe.get_doc({
            "doctype": "Hisabi Category", "client_id": f"c-{frappe.generate_hash(6)}",
            "category_name": "مطاعم", "kind": "expense",
            "user": self.user.name, "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_receipt_happy_path(self):
        import hisabi_backend.api.v1.ai_advisor as mod
        from hisabi_backend.api.v1.ai_advisor import parse_receipt_image

        orig = mod._vision_openai
        mod._vision_openai = lambda img, mime, prompt, settings: json.dumps({"transactions": [
            {"amount": "120", "currency": "SAR", "transaction_type": "expense",
             "description": "عشاء", "category": "مطاعم", "confidence": 0.9},
        ]})
        try:
            with _MockAISettings(enable=True, key="sk-x", provider="openai"):
                r = parse_receipt_image(
                    image_base64=base64.b64encode(b"jpeg-bytes").decode(),
                    wallet_id=self.wallet_id, mime_type="image/jpeg", device_id=self.device_id,
                )
            self.assertEqual(r["status"], "ok")
            self.assertEqual(len(r["transactions"]), 1)
            self.assertEqual(r["transactions"][0]["amount"], 120.0)
            self.assertEqual(r["transactions"][0]["category"], "مطاعم")
        finally:
            mod._vision_openai = orig

    def test_receipt_empty_image_raises(self):
        from hisabi_backend.api.v1.ai_advisor import parse_receipt_image

        with _MockAISettings(enable=True, key="sk-x"):
            with self.assertRaises(frappe.ValidationError):
                parse_receipt_image(image_base64="", wallet_id=self.wallet_id, device_id=self.device_id)

    def test_receipt_ai_disabled_raises(self):
        from hisabi_backend.api.v1.ai_advisor import parse_receipt_image

        with _MockAISettings(enable=False):
            with self.assertRaises(frappe.ValidationError):
                parse_receipt_image(
                    image_base64=base64.b64encode(b"x").decode(),
                    wallet_id=self.wallet_id, device_id=self.device_id,
                )


class TestAdvisorPersona(FrappeTestCase):
    def test_persona_tone_injected_into_prompt(self):
        from hisabi_backend.api.v1.ai_advisor import _build_financial_advisor_system_prompt

        ctx = {
            "base_currency": "SAR", "income_30d": 0, "expense_30d": 0, "net_30d": 0,
            "top_categories": [], "active_goals": [], "debts": [], "recent_transactions": [],
        }
        self.assertIn("خبير مالي تحليلي", _build_financial_advisor_system_prompt(ctx, persona="analyst"))
        self.assertIn("رفيق ودود", _build_financial_advisor_system_prompt(ctx, persona="friendly"))
        self.assertIn("مدرّب مالي", _build_financial_advisor_system_prompt(ctx, persona="coach"))
        # unknown / missing persona → analyst default
        self.assertIn("خبير مالي تحليلي", _build_financial_advisor_system_prompt(ctx, persona="???"))
        self.assertIn("خبير مالي تحليلي", _build_financial_advisor_system_prompt(ctx))


class TestChatActionRobustness(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        if not frappe.db.table_exists("Hisabi AI Chat Session"):
            self.skipTest("chat table not migrated")
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("ai_chat_action")

    def tearDown(self):
        frappe.set_user("Administrator")

    def _send(self, ai_text):
        from hisabi_backend.api.v1.ai_advisor import chat
        with _MockAISettings(enable=True), _MockAI(ai_text):
            return chat(wallet_id=self.wallet_id, message="ساعدني", device_id=self.device_id)

    def test_malformed_action_type_does_not_crash(self):
        # AI emits an ACTION whose `type` is an object → must be dropped, chat still succeeds.
        r = self._send('نصيحة.\nACTION::{"type": {}, "label": "x", "payload": {}}')
        self.assertEqual(r["status"], "ok")
        self.assertIsNone(r["action"])

    def test_import_batch_fix_action_saves_without_select_error(self):
        # A valid client action not present in the message Select must still save (type coerced to "").
        act = {"type": "import_batch_fix", "label": "تصحيح", "description": "d", "payload": {"corrections": []}}
        r = self._send("تصحيح.\nACTION::" + json.dumps(act, ensure_ascii=False))
        self.assertEqual(r["status"], "ok")
        self.assertIsNotNone(r["action"])
        self.assertEqual(r["action"]["type"], "import_batch_fix")
