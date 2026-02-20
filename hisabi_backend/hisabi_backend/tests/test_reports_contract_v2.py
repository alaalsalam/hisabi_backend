import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.reports_finance import (
    fx_rates_list,
    fx_rates_seed_defaults,
    fx_rates_upsert,
    report_cashflow,
    report_category_breakdown,
    report_summary,
    report_trends,
)
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestReportsContractV2(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"reports_v2_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Reports",
                "last_name": "Tester",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123A!")
        self.user = user.name

        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        frappe.set_user(self.user)
        self.wallet_id = ensure_default_wallet_for_user(self.user, device_id=self.device_id)
        token, _device = issue_device_token_for_device(
            user=self.user,
            device_id=self.device_id,
            platform="android",
            device_name="Pixel 8",
            wallet_id=self.wallet_id,
        )
        self.device_token = token

        frappe.local.request = type(
            "obj",
            (object,),
            {"headers": {"Authorization": f"Bearer {self.device_token}"}},
        )()

    def _push(self, items):
        response = sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            if getattr(response, "status_code", 200) != 200:
                raise AssertionError(f"sync_push failed: {payload}")
            response = payload.get("message", payload)
        results = (response or {}).get("results") or []
        for result in results:
            self.assertIn(result.get("status"), {"accepted", "duplicate"}, msg=f"Unexpected push status: {response}")
        return response

    def _ensure_wallet_base_currency(self, currency: str):
        existing_name = frappe.get_value(
            "Hisabi Settings",
            {"wallet_id": self.wallet_id, "user": self.user, "is_deleted": 0},
        )
        if existing_name:
            doc = frappe.get_doc("Hisabi Settings", existing_name)
        else:
            doc = frappe.new_doc("Hisabi Settings")
            doc.client_id = f"settings-{self.wallet_id}"
            doc.name = doc.client_id
            doc.user = self.user
            doc.wallet_id = self.wallet_id
        doc.base_currency = currency
        apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
        doc.save(ignore_permissions=True)

    def test_reports_require_wallet_id_422(self):
        summary = report_summary(device_id=self.device_id)
        self.assertEqual(getattr(summary, "status_code", None), 422)

        cash = report_cashflow(device_id=self.device_id)
        self.assertEqual(getattr(cash, "status_code", None), 422)

        breakdown = report_category_breakdown(device_id=self.device_id)
        self.assertEqual(getattr(breakdown, "status_code", None), 422)

        trends = report_trends(device_id=self.device_id)
        self.assertEqual(getattr(trends, "status_code", None), 422)

    def test_fx_upsert_list_and_report_conversion_warning(self):
        self._ensure_wallet_base_currency("EUR")
        base_currency = "EUR"
        tx_currency = "USD"

        self._push(
            [
                {
                    "op_id": "op-r2-acc-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-r2-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-r2-1",
                        "account_name": "Main",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 0,
                    },
                },
                {
                    "op_id": "op-r2-tx-usd",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-r2-usd",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-r2-usd",
                        "transaction_type": "income",
                        "date_time": now_datetime().isoformat(),
                        "amount": 10,
                        "currency": tx_currency,
                        "account": "acc-r2-1",
                    },
                },
            ]
        )
        summary_without_rate = report_summary(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertEqual((summary_without_rate.get("totals") or {}).get("income"), 0)
        warnings = summary_without_rate.get("warnings") or []
        self.assertTrue(any((w.get("code") == "fx_missing") for w in warnings))

        upsert = fx_rates_upsert(
            wallet_id=self.wallet_id,
            base_currency=tx_currency,
            quote_currency=base_currency,
            rate=3.75,
            effective_date=now_datetime().isoformat(),
            device_id=self.device_id,
        )
        self.assertIn("rate", upsert)

        listed = fx_rates_list(
            wallet_id=self.wallet_id,
            base_currency=tx_currency,
            quote_currency=base_currency,
            device_id=self.device_id,
        )
        self.assertGreaterEqual(listed.get("count") or 0, 1)
        self.assertTrue(all((row.get("wallet_id") == self.wallet_id) for row in listed.get("rates") or []))

        summary_with_rate = report_summary(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertAlmostEqual((summary_with_rate.get("totals") or {}).get("income") or 0, 37.5, places=2)

    def test_fx_seed_defaults_creates_wallet_scoped_default_rows(self):
        self._ensure_wallet_base_currency("SAR")
        seeded = fx_rates_seed_defaults(
            wallet_id=self.wallet_id,
            base_currency="SAR",
            enabled_currencies='["SAR","USD","YER"]',
            device_id=self.device_id,
        )
        seed_meta = seeded.get("seed") or {}
        self.assertGreaterEqual(int(seed_meta.get("seeded") or 0), 1)

        listed = fx_rates_list(wallet_id=self.wallet_id, device_id=self.device_id)
        rows = listed.get("rates") or []
        self.assertTrue(any((row.get("source") == "default") for row in rows))
        self.assertTrue(all((row.get("wallet_id") == self.wallet_id) for row in rows))

    def test_fx_seed_defaults_does_not_override_custom_rate(self):
        self._ensure_wallet_base_currency("SAR")
        upsert = fx_rates_upsert(
            wallet_id=self.wallet_id,
            base_currency="USD",
            quote_currency="SAR",
            rate=4.2,
            source="custom",
            effective_date=now_datetime().isoformat(),
            device_id=self.device_id,
        )
        self.assertIn("rate", upsert)

        seeded = fx_rates_seed_defaults(
            wallet_id=self.wallet_id,
            base_currency="SAR",
            enabled_currencies="SAR,USD",
            overwrite_defaults=1,
            device_id=self.device_id,
        )
        self.assertIn("seed", seeded)

        rows = frappe.get_all(
            "Hisabi FX Rate",
            filters={
                "wallet_id": self.wallet_id,
                "base_currency": "USD",
                "quote_currency": "SAR",
                "is_deleted": 0,
            },
            fields=["name", "rate", "source"],
            order_by="effective_date desc, server_modified desc, name desc",
            limit_page_length=1,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].get("source") or "").lower(), "custom")
        self.assertAlmostEqual(float(rows[0].get("rate") or 0), 4.2, places=4)

    def test_sync_push_fx_update_ignores_pull_only_metadata_fields(self):
        upsert = fx_rates_upsert(
            wallet_id=self.wallet_id,
            base_currency="USD",
            quote_currency="SAR",
            rate=3.75,
            source="custom",
            effective_date=now_datetime().isoformat(),
            device_id=self.device_id,
        )
        row = upsert.get("rate") or {}
        entity_id = row.get("client_id")
        base_version = int(row.get("doc_version") or 1)

        response = self._push(
            [
                {
                    "op_id": f"op-r2-fx-meta-{frappe.generate_hash(length=8)}",
                    "entity_type": "Hisabi FX Rate",
                    "entity_id": entity_id,
                    "operation": "update",
                    "base_version": base_version,
                    "payload": {
                        "client_id": entity_id,
                        "base_currency": "USD",
                        "quote_currency": "SAR",
                        "rate": 3.8,
                        "effective_date": now_datetime().isoformat(),
                        "source": "custom",
                        "doc_version": 999,
                        "server_modified": "2099-01-01 00:00:00.000000",
                    },
                }
            ]
        )
        result = (response.get("results") or [{}])[0]
        self.assertEqual(result.get("status"), "accepted")

        updated = frappe.get_all(
            "Hisabi FX Rate",
            filters={"wallet_id": self.wallet_id, "client_id": entity_id, "is_deleted": 0},
            fields=["rate"],
            order_by="effective_date desc, server_modified desc, name desc",
            limit_page_length=1,
        )
        self.assertEqual(len(updated), 1)
        self.assertAlmostEqual(float(updated[0].get("rate") or 0), 3.8, places=4)

    def test_report_filters_and_trends_contract(self):
        self._ensure_wallet_base_currency("SAR")

        day_one = now_datetime()
        day_two = add_days(day_one, 1)

        self._push(
            [
                {
                    "op_id": "op-r3-acc-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-r3-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-r3-1",
                        "account_name": "Wallet",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 0,
                    },
                },
                {
                    "op_id": "op-r3-cat-1",
                    "entity_type": "Hisabi Category",
                    "entity_id": "cat-r3-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "cat-r3-1",
                        "category_name": "Food",
                        "kind": "expense",
                    },
                },
                {
                    "op_id": "op-r3-tx-expense",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-r3-expense",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-r3-expense",
                        "transaction_type": "expense",
                        "date_time": day_one.isoformat(),
                        "amount": 20,
                        "currency": "SAR",
                        "account": "acc-r3-1",
                        "category": "cat-r3-1",
                    },
                },
                {
                    "op_id": "op-r3-tx-income",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-r3-income",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-r3-income",
                        "transaction_type": "income",
                        "date_time": day_two.isoformat(),
                        "amount": 50,
                        "currency": "SAR",
                        "account": "acc-r3-1",
                    },
                },
            ]
        )

        breakdown = report_category_breakdown(
            wallet_id=self.wallet_id,
            device_id=self.device_id,
            date_from=day_one.date().isoformat(),
            date_to=day_two.date().isoformat(),
            type="expense",
            category_id="cat-r3-1",
        )
        self.assertIn("categories", breakdown)
        self.assertEqual((breakdown.get("totals") or {}).get("expense"), 20)
        self.assertIn("warnings", breakdown)

        cashflow = report_cashflow(
            wallet_id=self.wallet_id,
            device_id=self.device_id,
            date_from=day_one.date().isoformat(),
            date_to=day_two.date().isoformat(),
            account_id="acc-r3-1",
            type="income,expense",
        )
        self.assertIn("points", cashflow)
        self.assertIn("totals", cashflow)
        self.assertIn("warnings", cashflow)

        trends = report_trends(
            wallet_id=self.wallet_id,
            device_id=self.device_id,
            date_from=day_one.date().isoformat(),
            date_to=day_two.date().isoformat(),
            granularity="weekly",
        )
        self.assertEqual(trends.get("granularity"), "weekly")
        self.assertIn("points", trends)
        self.assertIn("totals", trends)
        self.assertIn("warnings", trends)
