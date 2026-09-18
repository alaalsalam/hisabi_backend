"""Regression tests for AI Advisor bugs fixed on 2026-07-03.

These guard three production bugs that the existing (fully-mocked) AI tests could
not catch, because they all patch `_get_ai_settings` / `_call_ai`:

  1. `_get_ai_settings` read the encrypted Password field via the raw attribute
     (`s.openai_api_key`) which returns a MASKED '****' string, not the secret,
     so a masked value was sent to OpenAI → 401. Fixed to use `get_password()`.

  2. `_get_wallet_financial_context` queried `Hisabi Debt` with a non-existent
     column `party_name` (real column: `counterparty_name`) → MySQL 1054.

  3. Same function queried `Hisabi Transaction` with a non-existent column
     `description` (real column: `note`) → MySQL 1054.

Bugs 2/3 only surface against the real DB schema, so these tests exercise the
REAL queries with REAL data (no mocking of the settings/context helpers).
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase

from hisabi_backend.install import ensure_roles
# Reuse the authenticated-user fixture from the main AI advisor test module.
from hisabi_backend.hisabi_backend.tests.test_ai_advisor import _make_authed_user


class TestAISettingsDecryption(FrappeTestCase):
    """Guards bug #1 — the API key must be decrypted, never returned masked."""

    def setUp(self):
        ensure_roles()
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("ai_reg_key")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_get_ai_settings_returns_decrypted_key_not_masked(self):
        from hisabi_backend.api.v1.ai_advisor import _get_ai_settings

        real_key = "sk-proj-REG-" + frappe.generate_hash(length=16)
        s = frappe.get_single("Hisabi AI Settings")
        s.flags.ignore_validate = True
        s.enable_ai = 1
        s.openai_api_key = real_key
        s.gemini_api_key = ""
        s.save(ignore_permissions=True)
        frappe.clear_cache(doctype="Hisabi AI Settings")

        cfg = _get_ai_settings()

        # With the bug, the raw attribute returns '*' * len(key); the fix uses
        # get_password() which returns the true secret.
        self.assertEqual(cfg["openai_api_key"], real_key)
        self.assertNotIn("*", cfg["openai_api_key"])


class TestFinancialContextColumns(FrappeTestCase):
    """Guards bugs #2/#3 — real column names in the financial-context queries."""

    def setUp(self):
        ensure_roles()
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("ai_reg_ctx")
        self._acc = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-reg-{frappe.generate_hash(6)}",
                "account_name": "Cash", "account_type": "cash",
                "currency": "SAR", "opening_balance": 0,
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_context_builds_and_maps_debt_and_tx_columns(self):
        from hisabi_backend.api.v1.ai_advisor import _get_wallet_financial_context

        # Debt uses counterparty_name (NOT party_name).
        frappe.get_doc(
            {
                "doctype": "Hisabi Debt",
                "client_id": f"debt-reg-{frappe.generate_hash(6)}",
                "debt_name": "سلفة أحمد", "direction": "owe",
                "principal_amount": 500, "remaining_amount": 500,
                "counterparty_name": "أحمد", "currency": "SAR", "status": "active",
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        # Transaction stores its label in `note` (NOT description).
        frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": f"tx-reg-{frappe.generate_hash(8)}",
                "amount": 120, "currency": "SAR", "transaction_type": "expense",
                "note": "فاتورة كهرباء", "date_time": frappe.utils.now_datetime(),
                "account": self._acc.name,
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        # The real queries run here — a wrong column name would raise MySQL 1054.
        ctx = _get_wallet_financial_context(self.wallet_id, self.user.name)

        self.assertTrue(
            any(d.get("party_name") == "أحمد" for d in ctx["debts"]),
            "debt.counterparty_name must map to context party_name",
        )
        self.assertTrue(
            any(t.get("description") == "فاتورة كهرباء" for t in ctx["recent_transactions"]),
            "transaction.note must map to context description",
        )

    def test_context_on_empty_wallet_does_not_raise(self):
        # Even with no debts/transactions, the SELECT still names every column,
        # so a wrong column name would raise regardless of data.
        from hisabi_backend.api.v1.ai_advisor import _get_wallet_financial_context

        ctx = _get_wallet_financial_context(self.wallet_id, self.user.name)
        for key in ("income_30d", "expense_30d", "net_30d", "top_categories",
                    "active_goals", "debts", "recent_transactions"):
            self.assertIn(key, ctx)
