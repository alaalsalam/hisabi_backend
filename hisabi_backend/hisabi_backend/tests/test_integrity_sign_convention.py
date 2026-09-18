"""Locks the amount_base sign convention and the integrity self-heal.

amount_base is stored SIGNED (negative for expense, positive for income, 0 for transfer); `amount`
is always a positive magnitude. Every AGGREGATION that reports a magnitude (budget spent, report
expense totals, the advisor's expense/net/savings) must take ABS — summing the raw signed value
produced negative budget spend, negative report expense, and an inverted advisor net/savings_rate.

These tests force a negative amount_base (the real imported-data shape the default sync path never
produces) and assert every consumer reports a positive magnitude, plus that the integrity verifier
detects drift, heals the safe derivations, and refuses to destructively rewrite account balances.
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils import add_days, add_months, flt, now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import (
    heal_integrity,
    reconcile_account_balance,
    verify_integrity,
    wallet_create,
)
from hisabi_backend.api.v1.ai_advisor import (
    _build_financial_advisor_system_prompt,
    _get_wallet_financial_context,
)
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.reports_finance import category_breakdown
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.domain import integrity
from hisabi_backend.domain.recalc_engine import recalc_budget_spent
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.sync_common import apply_common_sync_fields


class TestIntegritySignConvention(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"sign_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Sign",
            "last_name": "Tester",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(self.device_id, "android", "Pixel 8")
        self.device_token = device.get("device_token")
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}}
        )()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Sign Wallet", device_id=self.device_id)
        self._ensure_wallet_base_currency("SAR")

    def _ensure_wallet_base_currency(self, currency):
        existing_name = frappe.get_value(
            "Hisabi Settings",
            {"wallet_id": self.wallet_id, "user": self.user.name, "is_deleted": 0},
        )
        if existing_name:
            doc = frappe.get_doc("Hisabi Settings", existing_name)
        else:
            doc = frappe.new_doc("Hisabi Settings")
            doc.client_id = f"settings-{self.wallet_id}"
            doc.name = doc.client_id
            doc.user = self.user.name
            doc.wallet_id = self.wallet_id
        doc.base_currency = currency
        apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
        doc.save(ignore_permissions=True)

    def _push(self, items):
        return sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)

    def _tx_name(self, client_id):
        return frappe.get_value(
            "Hisabi Transaction", {"client_id": client_id, "wallet_id": self.wallet_id}
        )

    def _set_base(self, client_id, value):
        """Force amount_base to the SIGNED value real imported data carries."""
        frappe.db.set_value("Hisabi Transaction", self._tx_name(client_id), "amount_base", value)

    def _account(self, client_id, currency="SAR", opening=0):
        return {
            "op_id": f"op-{client_id}",
            "entity_type": "Hisabi Account",
            "entity_id": client_id,
            "operation": "create",
            "payload": {
                "client_id": client_id, "account_name": client_id, "account_type": "cash",
                "currency": currency, "opening_balance": opening,
            },
        }

    def _category(self, client_id, kind="expense"):
        return {
            "op_id": f"op-{client_id}",
            "entity_type": "Hisabi Category",
            "entity_id": client_id,
            "operation": "create",
            "payload": {"client_id": client_id, "category_name": client_id, "kind": kind},
        }

    def _expense(self, client_id, account, category, amount, currency="SAR", when=None):
        return {
            "op_id": f"op-{client_id}",
            "entity_type": "Hisabi Transaction",
            "entity_id": client_id,
            "operation": "create",
            "payload": {
                "client_id": client_id, "transaction_type": "expense",
                "date_time": when or now_datetime(), "amount": amount, "currency": currency,
                "account": account, "category": category,
            },
        }

    def _income(self, client_id, account, amount, currency="SAR", when=None):
        return {
            "op_id": f"op-{client_id}",
            "entity_type": "Hisabi Transaction",
            "entity_id": client_id,
            "operation": "create",
            "payload": {
                "client_id": client_id, "transaction_type": "income",
                "date_time": when or now_datetime(), "amount": amount, "currency": currency,
                "account": account,
            },
        }

    # ---- budget spent ----------------------------------------------------

    def test_budget_spent_is_positive_when_amount_base_is_signed(self):
        start = now_datetime()
        end = add_months(start, 1)
        self._push([
            self._account("acc-s1"),
            self._category("cat-s1"),
            {
                "op_id": "op-bud-s1", "entity_type": "Hisabi Budget", "entity_id": "bud-s1",
                "operation": "create",
                "payload": {
                    "client_id": "bud-s1", "budget_name": "Food", "period": "monthly",
                    "scope_type": "category", "category": "cat-s1", "currency": "SAR",
                    "amount": 500, "start_date": start, "end_date": end,
                },
            },
        ])
        self._push([self._expense("tx-s1", "acc-s1", "cat-s1", 120)])
        self._set_base("tx-s1", -120)  # signed, as stored in real data

        budget_name = frappe.get_value("Hisabi Budget", {"client_id": "bud-s1", "wallet_id": self.wallet_id})
        recalc_budget_spent(self.user.name, budget_name)
        spent = flt(frappe.db.get_value("Hisabi Budget", budget_name, "spent_amount"))
        self.assertEqual(spent, 120.0, "budget spent must be a POSITIVE magnitude, not the signed sum")

    # ---- report totals (cross-currency exercises the amount_base branch) --

    def test_report_expense_total_positive_with_signed_base(self):
        start = now_datetime()
        end = add_months(start, 1)
        self._push([self._account("acc-usd", currency="USD"), self._category("cat-u1")])
        # USD tx in a SAR-base wallet → _tx_amount_in_base must use amount_base (not `amount`).
        self._push([self._expense("tx-u1", "acc-usd", "cat-u1", 30, currency="USD")])
        self._set_base("tx-u1", -120)  # 30 USD == 120 SAR, stored signed

        breakdown = category_breakdown(
            from_date=start.isoformat(), to_date=end.isoformat(),
            wallet_id=self.wallet_id, device_id=self.device_id,
        )
        totals = breakdown.get("totals") or {}
        self.assertEqual(flt(totals.get("expense")), 120.0,
                         "cross-currency expense total must be a positive base-currency magnitude")

    # ---- advisor context: net / savings must not invert ------------------

    def test_advisor_context_expense_net_savings_are_correct(self):
        self._push([self._account("acc-a1"), self._category("cat-a1")])
        self._push([
            self._income("tx-inc", "acc-a1", 1000),
            self._expense("tx-exp", "acc-a1", "cat-a1", 300),
        ])
        self._set_base("tx-inc", 1000)   # income positive
        self._set_base("tx-exp", -300)   # expense signed negative

        ctx = _get_wallet_financial_context(self.wallet_id, self.user.name)
        self.assertEqual(flt(ctx["expense_30d"]), 300.0, "expense_30d must be positive magnitude")
        self.assertEqual(flt(ctx["income_30d"]), 1000.0)
        self.assertEqual(flt(ctx["net_30d"]), 700.0, "net = income - expense, not income + |expense|")
        self.assertAlmostEqual(flt(ctx["savings_rate"]), 0.7, places=2)
        self.assertTrue(ctx["has_recent_activity"])

    # ---- advisor data-recency: the 'income 0' presenting symptom ---------

    def test_advisor_reports_data_recency_instead_of_zero(self):
        old = add_days(now_datetime(), -60)  # outside the 30-day window
        self._push([self._account("acc-o1"), self._category("cat-o1")])
        self._push([
            self._income("tx-old-inc", "acc-o1", 5000, when=old),
            self._expense("tx-old-exp", "acc-o1", "cat-o1", 2000, when=old),
        ])
        self._set_base("tx-old-inc", 5000)
        self._set_base("tx-old-exp", -2000)

        ctx = _get_wallet_financial_context(self.wallet_id, self.user.name)
        self.assertEqual(flt(ctx["income_30d"]), 0.0)
        self.assertFalse(ctx["has_recent_activity"])
        self.assertIsNotNone(ctx["last_tx_date"], "must surface the last transaction date")
        self.assertEqual(flt(ctx["all_time_income"]), 5000.0)
        self.assertEqual(flt(ctx["all_time_expense"]), 2000.0)

        prompt = _build_financial_advisor_system_prompt(ctx)
        self.assertIn("آخر 30 يوماً", prompt, "prompt must explain the empty window, not imply zero income")

    # ---- integrity verify + safe heal ------------------------------------

    def test_verify_detects_and_heals_negative_budget(self):
        start = now_datetime()
        end = add_months(start, 1)
        self._push([
            self._account("acc-h1"),
            self._category("cat-h1"),
            {
                "op_id": "op-bud-h1", "entity_type": "Hisabi Budget", "entity_id": "bud-h1",
                "operation": "create",
                "payload": {
                    "client_id": "bud-h1", "budget_name": "Heal", "period": "monthly",
                    "scope_type": "category", "category": "cat-h1", "currency": "SAR",
                    "amount": 500, "start_date": start, "end_date": end,
                },
            },
        ])
        self._push([self._expense("tx-h1", "acc-h1", "cat-h1", 200)])
        budget_name = frappe.get_value("Hisabi Budget", {"client_id": "bud-h1", "wallet_id": self.wallet_id})
        # Corrupt the stored aggregate to the old signed value.
        frappe.db.set_value("Hisabi Budget", budget_name, "spent_amount", -200)

        report = integrity.verify_wallet(self.wallet_id)
        self.assertFalse(report["ok"])
        budget_drift = next(d for d in report["drifts"] if d["entity_type"] == "budget")
        self.assertTrue(budget_drift["healable"])
        self.assertEqual(budget_drift["expected"], 200.0)

        integrity.heal_wallet(self.wallet_id)
        healed = flt(frappe.db.get_value("Hisabi Budget", budget_name, "spent_amount"))
        self.assertEqual(healed, 200.0)
        self.assertTrue(integrity.verify_wallet(self.wallet_id)["ok"])

    def test_account_phantom_is_reported_not_destructively_zeroed(self):
        self._push([self._account("acc-p1", opening=0)])
        acc_name = frappe.get_value("Hisabi Account", {"client_id": "acc-p1", "wallet_id": self.wallet_id})
        # A nonzero balance with no backing ledger (an unrecorded opening balance).
        frappe.db.set_value("Hisabi Account", acc_name, "current_balance", 5000)

        report = integrity.verify_wallet(self.wallet_id)
        acc_drift = next(d for d in report["drifts"] if d["entity_type"] == "account")
        self.assertFalse(acc_drift["healable"], "account balances must never be auto-zeroed")

        result = integrity.heal_wallet(self.wallet_id)
        self.assertEqual(result["healed_count"], 0)
        self.assertTrue(any(d["entity_type"] == "account" for d in result["reported_only"]))
        # The real money must still be there.
        self.assertEqual(flt(frappe.db.get_value("Hisabi Account", acc_name, "current_balance")), 5000.0)

    # ---- whitelisted endpoint (auth + scope) -----------------------------

    def _act_as_self(self):
        frappe.set_user(self.user.name)
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}}
        )()

    def _make_other_wallet(self):
        email = f"other_{frappe.generate_hash(length=6)}@example.com"
        u = frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "Other", "last_name": "Wallet",
            "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(u.name, "test123")
        frappe.set_user(u.name)
        dev = f"device-{frappe.generate_hash(length=6)}"
        tok = register_device(dev, "android", "Pixel").get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {tok}"}})()
        wid = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wid, wallet_name="Other", device_id=dev)
        self._act_as_self()
        return wid

    def test_verify_endpoint_returns_report_for_member(self):
        report = verify_integrity(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertIn("ok", report)
        self.assertIn("drifts", report)
        self.assertEqual(report["wallet_id"], self.wallet_id)

    def test_verify_endpoint_denies_non_member(self):
        other_wallet = self._make_other_wallet()
        with self.assertRaises(frappe.PermissionError):
            verify_integrity(wallet_id=other_wallet, device_id=self.device_id)

    def test_heal_endpoint_denies_non_member(self):
        other_wallet = self._make_other_wallet()
        with self.assertRaises(frappe.PermissionError):
            heal_integrity(wallet_id=other_wallet, device_id=self.device_id)

    def test_endpoint_requires_wallet_id_never_returns_fleet_data(self):
        # A missing wallet_id must throw — it must NOT fall through to the domain layer's
        # fleet-wide (wallet_id=None) scan and leak other users' data.
        with self.assertRaises(frappe.ValidationError):
            verify_integrity(device_id=self.device_id)
        with self.assertRaises(frappe.ValidationError):
            heal_integrity(device_id=self.device_id)

    # ---- account reconcile + write-path soundness ------------------------

    def _acc_name(self, client_id):
        return frappe.get_value("Hisabi Account", {"client_id": client_id, "wallet_id": self.wallet_id})

    def test_reconcile_adopts_unexplained_balance_as_opening(self):
        self._push([self._account("acc-r0", opening=0)])
        name = self._acc_name("acc-r0")
        # Phantom: a balance with no backing ledger and opening=0.
        frappe.db.set_value("Hisabi Account", name, "current_balance", 3000)

        res = integrity.reconcile_account_opening(name)
        self.assertEqual(res["opening_balance"], 3000.0)
        self.assertEqual(res["current_balance"], 3000.0, "displayed balance must be preserved")
        self.assertTrue(integrity.verify_wallet(self.wallet_id)["ok"])

    def test_reconcile_handles_account_with_only_soft_deleted_ledger(self):
        # The most plausible original shape of الكريمي: an account whose transactions were all
        # soft-deleted, leaving a stranded current_balance with no live ledger. Reconcile must fold
        # the full balance into opening (like the phantom case) and land consistent.
        self._push([self._account("acc-r3", opening=0), self._category("cat-r3")])
        self._push([self._expense("tx-r3", "acc-r3", "cat-r3", 100)])  # current -> -100
        name = self._acc_name("acc-r3")
        tx_name = self._tx_name("tx-r3")
        frappe.db.set_value("Hisabi Transaction", tx_name, "is_deleted", 1)  # only ledger row gone
        frappe.db.set_value("Hisabi Account", name, "current_balance", 14000)  # stranded balance

        res = integrity.reconcile_account_opening(name)
        self.assertEqual(res["opening_balance"], 14000.0, "all-soft-deleted ledger → full balance becomes opening")
        self.assertEqual(res["current_balance"], 14000.0, "displayed balance preserved")
        self.assertTrue(integrity.verify_wallet(self.wallet_id)["ok"])

    def test_reconcile_preserves_balance_even_with_ledger(self):
        self._push([self._account("acc-r2", opening=0), self._category("cat-r2")])
        self._push([self._expense("tx-r2", "acc-r2", "cat-r2", 100)])  # ledger delta = -100
        name = self._acc_name("acc-r2")
        # Corrupt to an unexplained figure (should be -100; stored 900 → +1000 unexplained).
        frappe.db.set_value("Hisabi Account", name, "current_balance", 900)

        res = integrity.reconcile_account_opening(name)
        self.assertEqual(res["opening_balance"], 1000.0, "unexplained remainder folds into opening")
        self.assertEqual(res["current_balance"], 900.0, "current = opening + ledger, balance preserved")
        self.assertTrue(integrity.verify_wallet(self.wallet_id)["ok"])

    def test_editing_opening_balance_rederives_current_no_silent_drift(self):
        # Write-path soundness: changing opening_balance via a normal account update must
        # re-derive current_balance, so accounts can never silently drift going forward.
        self._push([self._account("acc-w1", opening=0), self._category("cat-w1")])
        self._push([self._expense("tx-w1", "acc-w1", "cat-w1", 100)])  # current -> -100
        name = self._acc_name("acc-w1")
        self.assertEqual(flt(frappe.db.get_value("Hisabi Account", name, "current_balance")), -100.0)

        # base_version must be the account's CURRENT version (recalc bumped it) or the optimistic
        # concurrency check rejects the edit as a conflict.
        base_version = int(frappe.db.get_value("Hisabi Account", name, "doc_version") or 0)
        self._push([{
            "op_id": "op-acc-w1-upd", "entity_type": "Hisabi Account", "entity_id": "acc-w1",
            "operation": "update", "base_version": base_version,
            "payload": {"client_id": "acc-w1", "currency": "SAR", "opening_balance": 500},
        }])
        self.assertEqual(
            flt(frappe.db.get_value("Hisabi Account", name, "current_balance")), 400.0,
            "current must be re-derived as opening(500) + ledger(-100)",
        )

    def test_reconcile_endpoint_member_ok_and_denies_non_member(self):
        self._push([self._account("acc-e1", opening=0)])
        name = self._acc_name("acc-e1")
        frappe.db.set_value("Hisabi Account", name, "current_balance", 250)

        res = reconcile_account_balance(
            wallet_id=self.wallet_id, account_id="acc-e1", device_id=self.device_id
        )
        self.assertEqual(res["current_balance"], 250.0)

        other_wallet = self._make_other_wallet()
        with self.assertRaises(frappe.PermissionError):
            reconcile_account_balance(wallet_id=other_wallet, account_id="acc-e1", device_id=self.device_id)

    def test_reconcile_endpoint_requires_ids(self):
        with self.assertRaises(frappe.ValidationError):
            reconcile_account_balance(account_id="acc-e1", device_id=self.device_id)  # no wallet_id
        with self.assertRaises(frappe.ValidationError):
            reconcile_account_balance(wallet_id=self.wallet_id, device_id=self.device_id)  # no account_id
