import json
from datetime import datetime, timezone

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.sync import _cursor_dt, sync_pull, sync_push as _sync_push
from hisabi_backend.install import ensure_roles


def sync_push(*args, **kwargs):
    response = _sync_push(*args, **kwargs)
    if isinstance(response, dict):
        return response
    if hasattr(response, "get_data"):
        payload = json.loads(response.get_data(as_text=True) or "{}")
        message = payload.get("message")
        if isinstance(message, dict):
            return message
        error_message = payload.get("message") or payload.get("error") or "sync_push_failed"
        status_code = getattr(response, "status_code", None)
        if status_code == 401:
            raise frappe.AuthenticationError(error_message)
        if status_code == 403:
            raise frappe.PermissionError(error_message)
        raise frappe.ValidationError(error_message)
    return response


class TestSyncV1(FrappeTestCase):
    def _pull_message(self, response):
        if isinstance(response, dict):
            return response
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            return payload.get("message", payload)
        return response

    def setUp(self):
        self._ensure_settings_profile_fields()
        ensure_roles()
        email = f"sync_test_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Sync",
            "last_name": "Tester",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(self.device_id, "android", "Pixel 8")
        self.assertTrue(device.get("wallet_id"))
        device_name = frappe.get_value("Hisabi Device", {"device_id": self.device_id})
        self.assertEqual(frappe.db.get_value("Hisabi Device", device_name, "wallet_id"), device.get("wallet_id"))
        self.device_token = device.get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Test Wallet", device_id=self.device_id)

    def _ensure_settings_profile_fields(self):
        custom_fields = [
            {
                "fieldname": "phone_number",
                "label": "Phone Number",
                "fieldtype": "Data",
                "insert_after": "locale",
            },
            {
                "fieldname": "notifications_preferences",
                "label": "Notifications Preferences",
                "fieldtype": "JSON",
                "insert_after": "phone_number",
            },
            {
                "fieldname": "enforce_fx",
                "label": "Enforce FX",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "notifications_preferences",
            },
        ]
        meta = frappe.get_meta("Hisabi Settings")
        for custom_field in custom_fields:
            if meta.has_field(custom_field["fieldname"]):
                continue
            create_custom_field("Hisabi Settings", custom_field, ignore_validate=True)
        frappe.clear_cache(doctype="Hisabi Settings")

    def test_sync_create_account_and_transaction(self):
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-1",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 100,
                        "client_modified_ms": 1700000000000,
                    },
                }
            ],
        )

        result = response["results"][0]
        self.assertEqual(result["status"], "accepted")

        account = frappe.get_doc("Hisabi Account", "acc-1")
        self.assertEqual(account.client_id, "acc-1")
        self.assertEqual(account.current_balance, 100)
        self.assertEqual(account.doc_version, 1)

        tx_response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-1",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-1",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 40,
                        "currency": "SAR",
                        "account": "acc-1",
                        "client_modified_ms": 1700000000001,
                    },
                }
            ],
        )

        tx_result = tx_response["results"][0]
        self.assertEqual(tx_result["status"], "accepted")

        account.reload()
        self.assertEqual(account.current_balance, 60)

    def test_sync_push_requires_bearer_token(self):
        frappe.local.request = type("obj", (object,), {"headers": {}})()
        with self.assertRaises(frappe.AuthenticationError):
            sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=[])

        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()

    def test_sync_push_rejects_revoked_device(self):
        device_name = frappe.get_value("Hisabi Device", {"device_id": self.device_id})
        device = frappe.get_doc("Hisabi Device", device_name)
        device.status = "revoked"
        device.save(ignore_permissions=True)

        with self.assertRaises(frappe.PermissionError):
            sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=[])

    def test_rate_limit_enforced(self):
        frappe.conf["hisabi_sync_rate_limit_max"] = 2
        frappe.conf["hisabi_sync_rate_limit_window_sec"] = 60
        try:
            sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=[])
            sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=[])
            with self.assertRaises(frappe.PermissionError):
                sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=[])
        finally:
            frappe.conf.pop("hisabi_sync_rate_limit_max", None)
            frappe.conf.pop("hisabi_sync_rate_limit_window_sec", None)

    def test_entity_type_validation(self):
        with self.assertRaises(frappe.ValidationError):
            sync_push(
                device_id=self.device_id,
                wallet_id=self.wallet_id,
                items=[
                    {
                        "op_id": "op-unknown-1",
                        "entity_type": "Unknown DocType",
                        "entity_id": "u-1",
                        "operation": "create",
                        "payload": {"client_id": "u-1"},
                    }
                ],
            )

    def test_items_cap_enforced(self):
        items = [
            {
                "op_id": f"op-{i}",
                "entity_type": "Hisabi Account",
                "entity_id": f"acc-{i}",
                "operation": "create",
                "payload": {"client_id": f"acc-{i}"},
            }
            for i in range(201)
        ]
        response = sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)
        self.assertEqual(response["results"][0]["status"], "error")
        self.assertEqual(response["results"][0]["error"], "too_many_items")

    def test_category_and_expense_transaction(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-cat-1",
                    "entity_type": "Hisabi Category",
                    "entity_id": "cat-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "cat-1",
                        "category_name": "Food",
                        "kind": "expense",
                    },
                },
                {
                    "op_id": "op-acc-cat",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-cat",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-cat",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 100,
                    },
                },
                {
                    "op_id": "op-tx-cat",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-cat",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-cat",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 10,
                        "currency": "SAR",
                        "account": "acc-cat",
                        "category": "cat-1",
                    },
                },
            ],
        )

        tx_name = frappe.get_value("Hisabi Transaction", {"client_id": "tx-cat", "wallet_id": self.wallet_id})
        self.assertTrue(tx_name)
        tx = frappe.get_doc("Hisabi Transaction", tx_name)
        self.assertEqual(tx.category, "cat-1")

    def test_category_pull_payload_has_stable_name_and_client_id(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-cat-stable-1",
                    "entity_type": "Hisabi Category",
                    "entity_id": "cat-stable-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "cat-stable-1",
                        "category_name": "Stable Category",
                        "kind": "expense",
                    },
                }
            ],
        )

        pull = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id))
        categories = [item for item in pull.get("items", []) if item.get("entity_type") == "Hisabi Category"]
        row = next((item for item in categories if item.get("client_id") == "cat-stable-1"), None)
        self.assertIsNotNone(row)
        payload = row.get("payload") or {}
        self.assertEqual(payload.get("client_id"), "cat-stable-1")
        self.assertEqual(payload.get("name"), "cat-stable-1")

    def test_bucket_rule_and_allocation_records(self):
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-bucket",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-bucket",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-bucket",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 100,
                    },
                },
                {
                    "op_id": "op-bucket-1",
                    "entity_type": "Hisabi Bucket",
                    "entity_id": "bucket-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "bucket-1",
                        "bucket_name": "Personal",
                    },
                },
                {
                    "op_id": "op-rule-1",
                    "entity_type": "Hisabi Allocation Rule",
                    "entity_id": "rule-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "rule-1",
                        "rule_name": "Default",
                        "scope_type": "global",
                    },
                },
                {
                    "op_id": "op-line-1",
                    "entity_type": "Hisabi Allocation Rule Line",
                    "entity_id": "line-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "line-1",
                        "rule": "rule-1",
                        "bucket": "bucket-1",
                        "percent": 50,
                    },
                },
                {
                    "op_id": "op-tx-alloc",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-alloc",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-alloc",
                        "transaction_type": "income",
                        "date_time": now_datetime(),
                        "amount": 50,
                        "currency": "SAR",
                        "account": "acc-bucket",
                    },
                },
                {
                    "op_id": "op-alloc-1",
                    "entity_type": "Hisabi Transaction Allocation",
                    "entity_id": "alloc-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "alloc-1",
                        "transaction": "tx-alloc",
                        "bucket": "bucket-1",
                        "percent": 50,
                        "amount": 25,
                        "currency": "SAR",
                        "amount_base": 25,
                    },
                },
            ],
        )

        statuses = [row["status"] for row in response["results"]]
        self.assertTrue(all(status == "accepted" for status in statuses))

    def test_transfer_updates_both_accounts(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-a",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-a",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-a",
                        "account_name": "Cash A",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 100,
                    },
                },
                {
                    "op_id": "op-acc-b",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-b",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-b",
                        "account_name": "Cash B",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 50,
                    },
                },
            ],
        )

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-transfer",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-transfer",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-transfer",
                        "transaction_type": "transfer",
                        "date_time": now_datetime(),
                        "amount": 20,
                        "currency": "SAR",
                        "account": "acc-a",
                        "to_account": "acc-b",
                    },
                }
            ],
        )

        acc_a = frappe.get_doc("Hisabi Account", "acc-a")
        acc_b = frappe.get_doc("Hisabi Account", "acc-b")
        self.assertEqual(acc_a.current_balance, 80)
        self.assertEqual(acc_b.current_balance, 70)

    def test_conflict_on_version_mismatch(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-2",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-2",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-2",
                        "account_name": "Bank",
                        "account_type": "bank",
                        "currency": "SAR",
                        "opening_balance": 0,
                    },
                }
            ],
        )

        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-2-update",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-2",
                    "operation": "update",
                    "base_version": 0,
                    "payload": {
                        "client_id": "acc-2",
                        "account_name": "Bank Updated",
                        "client_modified_ms": 1700000000002,
                    },
                }
            ],
        )

        result = response["results"][0]
        self.assertEqual(result["status"], "conflict")

    def test_sync_push_rejects_wallet_id_mismatch_with_custom_message_and_audit(self):
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-wallet-mismatch-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-wallet-mismatch",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-wallet-mismatch",
                        "wallet_id": "wallet-other",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                    },
                }
            ],
        )
        result = response["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "wallet_id_mismatch")
        self.assertIn("wallet_id_mismatch", result.get("error_message", ""))

    def test_sync_push_create_account_binds_wallet_id_from_request(self):
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-account-bind-wallet",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-bind-wallet",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-bind-wallet",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                    },
                }
            ],
        )
        result = response["results"][0]
        self.assertEqual(result["status"], "accepted")
        account = frappe.get_doc("Hisabi Account", "acc-bind-wallet")
        self.assertEqual(account.wallet_id, self.wallet_id)

    def test_sync_push_persists_settings_currency_fx_accounts_categories_transactions(self):
        suffix = frappe.generate_hash(length=6)
        settings_id = f"settings-{suffix}"
        custom_currency_id = f"cur-{suffix}"
        fx_rate_id = f"fx-{suffix}"
        account_base_id = f"acc-base-{suffix}"
        account_secondary_id = f"acc-usd-{suffix}"
        category_id = f"cat-{suffix}"
        tx_base_id = f"tx-base-{suffix}"
        tx_fx_id = f"tx-fx-{suffix}"

        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": f"op-settings-{suffix}",
                    "entity_type": "Hisabi Settings",
                    "entity_id": settings_id,
                    "operation": "create",
                    "payload": {
                        "client_id": settings_id,
                        "base_currency": "SAR",
                        "enabled_currencies": ["SAR", "USD"],
                        "locale": "en",
                        "phone_number": "+967700000123",
                        "notifications_preferences": ["debt_reminders", "budget_alerts"],
                        "enforce_fx": 1,
                        "week_start_day": 6,
                    },
                },
                {
                    "op_id": f"op-cur-{suffix}",
                    "entity_type": "Hisabi Custom Currency",
                    "entity_id": custom_currency_id,
                    "operation": "create",
                    "payload": {
                        "client_id": custom_currency_id,
                        "code": "USD",
                        "name_en": "US Dollar",
                        "name_ar": "دولار أمريكي",
                        "symbol": "$",
                        "decimals": 2,
                    },
                },
                {
                    "op_id": f"op-fx-{suffix}",
                    "entity_type": "Hisabi FX Rate",
                    "entity_id": fx_rate_id,
                    "operation": "create",
                    "payload": {
                        "client_id": fx_rate_id,
                        "base_currency": "SAR",
                        "quote_currency": "USD",
                        "rate": 0.2666,
                        "effective_date": now_datetime(),
                        "source": "custom",
                    },
                },
                {
                    "op_id": f"op-acc-base-{suffix}",
                    "entity_type": "Hisabi Account",
                    "entity_id": account_base_id,
                    "operation": "create",
                    "payload": {
                        "client_id": account_base_id,
                        "account_name": "Cash Base",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 200,
                    },
                },
                {
                    "op_id": f"op-acc-secondary-{suffix}",
                    "entity_type": "Hisabi Account",
                    "entity_id": account_secondary_id,
                    "operation": "create",
                    "payload": {
                        "client_id": account_secondary_id,
                        "account_name": "Cash USD",
                        "account_type": "cash",
                        "currency": "USD",
                        "opening_balance": 100,
                    },
                },
                {
                    "op_id": f"op-cat-{suffix}",
                    "entity_type": "Hisabi Category",
                    "entity_id": category_id,
                    "operation": "create",
                    "payload": {
                        "client_id": category_id,
                        "category_name": "General",
                        "kind": "expense",
                    },
                },
                {
                    "op_id": f"op-tx-base-{suffix}",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": tx_base_id,
                    "operation": "create",
                    "payload": {
                        "client_id": tx_base_id,
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 20,
                        "currency": "SAR",
                        "account": account_base_id,
                        "category": category_id,
                    },
                },
                {
                    "op_id": f"op-tx-fx-{suffix}",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": tx_fx_id,
                    "operation": "create",
                    "payload": {
                        "client_id": tx_fx_id,
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 10,
                        "currency": "USD",
                        "original_amount": 10,
                        "original_currency": "USD",
                        "converted_amount": 37.5,
                        "amount_base": 37.5,
                        "fx_rate_used": 3.75,
                        "account": account_base_id,
                        "category": category_id,
                    },
                },
            ],
        )
        self.assertTrue(all(row.get("status") == "accepted" for row in response.get("results", [])), response)

        settings_name = frappe.get_value("Hisabi Settings", {"client_id": settings_id, "wallet_id": self.wallet_id})
        custom_currency_name = frappe.get_value(
            "Hisabi Custom Currency", {"client_id": custom_currency_id, "wallet_id": self.wallet_id}
        )
        fx_rate_name = frappe.get_value("Hisabi FX Rate", {"client_id": fx_rate_id, "wallet_id": self.wallet_id})
        tx_base_name = frappe.get_value("Hisabi Transaction", {"client_id": tx_base_id, "wallet_id": self.wallet_id})
        tx_fx_name = frappe.get_value("Hisabi Transaction", {"client_id": tx_fx_id, "wallet_id": self.wallet_id})
        self.assertTrue(settings_name)
        self.assertTrue(custom_currency_name)
        self.assertTrue(fx_rate_name)
        self.assertTrue(tx_base_name)
        self.assertTrue(tx_fx_name)

        settings = frappe.get_doc("Hisabi Settings", settings_name)
        custom_currency = frappe.get_doc("Hisabi Custom Currency", custom_currency_name)
        fx_rate = frappe.get_doc("Hisabi FX Rate", fx_rate_name)
        account_base = frappe.get_doc("Hisabi Account", account_base_id)
        account_secondary = frappe.get_doc("Hisabi Account", account_secondary_id)
        category = frappe.get_doc("Hisabi Category", category_id)
        tx_base = frappe.get_doc("Hisabi Transaction", tx_base_name)
        tx_fx = frappe.get_doc("Hisabi Transaction", tx_fx_name)

        self.assertEqual(settings.wallet_id, self.wallet_id)
        self.assertEqual(custom_currency.wallet_id, self.wallet_id)
        self.assertEqual(fx_rate.wallet_id, self.wallet_id)
        self.assertEqual(account_base.wallet_id, self.wallet_id)
        self.assertEqual(account_secondary.wallet_id, self.wallet_id)
        self.assertEqual(category.wallet_id, self.wallet_id)
        self.assertEqual(tx_base.wallet_id, self.wallet_id)
        self.assertEqual(tx_fx.wallet_id, self.wallet_id)
        self.assertGreaterEqual(int(settings.doc_version or 0), 1)
        self.assertGreaterEqual(int(fx_rate.doc_version or 0), 1)
        self.assertGreaterEqual(int(tx_fx.doc_version or 0), 1)
        self.assertEqual(settings.phone_number, "+967700000123")
        self.assertEqual(int(settings.enforce_fx or 0), 1)
        notifications_preferences = settings.notifications_preferences
        if isinstance(notifications_preferences, str):
            parsed_notifications = json.loads(notifications_preferences)
        else:
            parsed_notifications = notifications_preferences or []
        self.assertIn("debt_reminders", parsed_notifications)
        self.assertIn("budget_alerts", parsed_notifications)
        self.assertEqual(tx_fx.account, account_base_id)
        self.assertEqual(tx_fx.category, category_id)
        self.assertEqual(str(tx_fx.currency or "").upper(), "USD")
        self.assertGreater(float(tx_fx.fx_rate_used or 0), 0)
        enabled_currencies = settings.enabled_currencies
        if isinstance(enabled_currencies, str):
            self.assertIn("USD", enabled_currencies)
        else:
            self.assertIn("USD", enabled_currencies or [])

    def test_sync_push_settings_update_accepts_camel_case_fields(self):
        suffix = frappe.generate_hash(length=6)
        settings_id = f"settings-camel-{suffix}"
        create_response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": f"op-settings-create-{suffix}",
                    "entity_type": "Hisabi Settings",
                    "entity_id": settings_id,
                    "operation": "create",
                    "payload": {
                        "client_id": settings_id,
                        "base_currency": "SAR",
                        "enabled_currencies": ["SAR", "USD"],
                        "locale": "ar-SA",
                    },
                }
            ],
        )
        self.assertEqual(create_response["results"][0]["status"], "accepted")
        create_doc_version = int(create_response["results"][0]["doc_version"] or 0)
        self.assertGreaterEqual(create_doc_version, 1)

        update_response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": f"op-settings-update-{suffix}",
                    "entity_type": "Hisabi Settings",
                    "entity_id": settings_id,
                    "operation": "update",
                    "base_version": create_doc_version,
                    "payload": {
                        "client_id": settings_id,
                        "phoneNumber": "+967700000555",
                        "notificationsPreferences": {"enabled": True, "debtReminders": False},
                        "enforceFx": 1,
                    },
                }
            ],
        )
        self.assertEqual(update_response["results"][0]["status"], "accepted")

        settings_name = frappe.get_value("Hisabi Settings", {"client_id": settings_id, "wallet_id": self.wallet_id})
        self.assertTrue(settings_name)
        settings = frappe.get_doc("Hisabi Settings", settings_name)
        self.assertEqual(settings.phone_number, "+967700000555")
        self.assertEqual(int(settings.enforce_fx or 0), 1)
        notifications_preferences = settings.notifications_preferences
        if isinstance(notifications_preferences, str):
            parsed_notifications = json.loads(notifications_preferences)
        else:
            parsed_notifications = notifications_preferences or {}
        self.assertEqual(parsed_notifications.get("enabled"), True)
        self.assertEqual(parsed_notifications.get("debtReminders"), False)

    def test_sync_push_rejects_sensitive_password_field_in_payload(self):
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-sensitive-payload-1",
                    "entity_type": "Hisabi Settings",
                    "entity_id": "settings-sensitive-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "settings-sensitive-1",
                        "base_currency": "SAR",
                        "password": "should-never-sync",
                    },
                }
            ],
        )
        result = response["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "sensitive_field_not_allowed")

    def test_sync_push_rejects_invalid_settings_optional_field_types(self):
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-settings-invalid-types-1",
                    "entity_type": "Hisabi Settings",
                    "entity_id": "settings-invalid-types-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "settings-invalid-types-1",
                        "base_currency": "SAR",
                        "phone_number": 123456,
                        "notifications_preferences": "bad-string",
                        "enforce_fx": "yes",
                    },
                }
            ],
        )
        result = response["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "invalid_field_type")
        detail = result.get("detail") or {}
        self.assertEqual(detail.get("phone_number"), "string")
        self.assertEqual(detail.get("notifications_preferences"), "json")
        self.assertEqual(detail.get("enforce_fx"), "number")

    def test_sync_pull_enforces_wallet_scope_for_fx_and_transactions(self):
        suffix = frappe.generate_hash(length=6)
        other_wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=other_wallet_id, wallet_name="Other Wallet", device_id=self.device_id)

        own_account = f"acc-own-{suffix}"
        own_category = f"cat-own-{suffix}"
        own_fx = f"fx-own-{suffix}"
        own_tx = f"tx-own-{suffix}"
        other_account = f"acc-other-{suffix}"
        other_category = f"cat-other-{suffix}"
        other_fx = f"fx-other-{suffix}"
        other_tx = f"tx-other-{suffix}"

        own_push = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": f"op-own-acc-{suffix}",
                    "entity_type": "Hisabi Account",
                    "entity_id": own_account,
                    "operation": "create",
                    "payload": {
                        "client_id": own_account,
                        "account_name": "Own Account",
                        "account_type": "cash",
                        "currency": "SAR",
                    },
                },
                {
                    "op_id": f"op-own-cat-{suffix}",
                    "entity_type": "Hisabi Category",
                    "entity_id": own_category,
                    "operation": "create",
                    "payload": {
                        "client_id": own_category,
                        "category_name": "Own Category",
                        "kind": "expense",
                    },
                },
                {
                    "op_id": f"op-own-fx-{suffix}",
                    "entity_type": "Hisabi FX Rate",
                    "entity_id": own_fx,
                    "operation": "create",
                    "payload": {
                        "client_id": own_fx,
                        "base_currency": "SAR",
                        "quote_currency": "USD",
                        "rate": 0.2666,
                    },
                },
                {
                    "op_id": f"op-own-tx-{suffix}",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": own_tx,
                    "operation": "create",
                    "payload": {
                        "client_id": own_tx,
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 12,
                        "currency": "SAR",
                        "account": own_account,
                        "category": own_category,
                    },
                },
            ],
        )
        self.assertTrue(all(row.get("status") == "accepted" for row in own_push.get("results", [])), own_push)

        other_push = sync_push(
            device_id=self.device_id,
            wallet_id=other_wallet_id,
            items=[
                {
                    "op_id": f"op-other-acc-{suffix}",
                    "entity_type": "Hisabi Account",
                    "entity_id": other_account,
                    "operation": "create",
                    "payload": {
                        "client_id": other_account,
                        "account_name": "Other Account",
                        "account_type": "cash",
                        "currency": "SAR",
                    },
                },
                {
                    "op_id": f"op-other-cat-{suffix}",
                    "entity_type": "Hisabi Category",
                    "entity_id": other_category,
                    "operation": "create",
                    "payload": {
                        "client_id": other_category,
                        "category_name": "Other Category",
                        "kind": "expense",
                    },
                },
                {
                    "op_id": f"op-other-fx-{suffix}",
                    "entity_type": "Hisabi FX Rate",
                    "entity_id": other_fx,
                    "operation": "create",
                    "payload": {
                        "client_id": other_fx,
                        "base_currency": "SAR",
                        "quote_currency": "EUR",
                        "rate": 0.24,
                    },
                },
                {
                    "op_id": f"op-other-tx-{suffix}",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": other_tx,
                    "operation": "create",
                    "payload": {
                        "client_id": other_tx,
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 15,
                        "currency": "SAR",
                        "account": other_account,
                        "category": other_category,
                    },
                },
            ],
        )
        self.assertTrue(all(row.get("status") == "accepted" for row in other_push.get("results", [])))

        pull = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id))
        items = pull.get("items") or []
        by_key = {(row.get("entity_type"), row.get("client_id")) for row in items}

        self.assertIn(("Hisabi Account", own_account), by_key)
        self.assertIn(("Hisabi Category", own_category), by_key)
        self.assertIn(("Hisabi FX Rate", own_fx), by_key)
        self.assertIn(("Hisabi Transaction", own_tx), by_key)
        self.assertNotIn(("Hisabi Account", other_account), by_key)
        self.assertNotIn(("Hisabi Category", other_category), by_key)
        self.assertNotIn(("Hisabi FX Rate", other_fx), by_key)
        self.assertNotIn(("Hisabi Transaction", other_tx), by_key)

        tx_row = next(
            row for row in items if row.get("entity_type") == "Hisabi Transaction" and row.get("client_id") == own_tx
        )
        tx_payload = tx_row.get("payload") or {}
        self.assertEqual(tx_payload.get("wallet_id"), self.wallet_id)
        self.assertEqual(tx_payload.get("account"), own_account)
        self.assertEqual(tx_payload.get("category"), own_category)

    def test_same_client_id_cross_wallet_mismatch_is_rejected(self):
        other_wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=other_wallet_id, wallet_name="Other Wallet", device_id=self.device_id)

        first = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-shared-id-wallet-a",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-shared-id",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-shared-id",
                        "account_name": "Wallet A Account",
                        "account_type": "cash",
                        "currency": "SAR",
                    },
                }
            ],
        )
        self.assertEqual(first["results"][0]["status"], "accepted")

        second = sync_push(
            device_id=self.device_id,
            wallet_id=other_wallet_id,
            items=[
                {
                    "op_id": "op-shared-id-wallet-b-mismatch",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-shared-id",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-shared-id",
                        "wallet_id": self.wallet_id,
                        "account_name": "Wallet B Account",
                        "account_type": "cash",
                        "currency": "SAR",
                    },
                }
            ],
        )
        result = second["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "wallet_id_mismatch")

    def test_sync_pull_returns_seed_warning_when_server_seed_records_missing(self):
        pull = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id))
        warnings = pull.get("warnings") or []
        self.assertIsInstance(warnings, list)
        self.assertTrue(any(w.get("code") == "seed_records_empty" for w in warnings))

    def test_pull_delete_and_cursor(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-3",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-3",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-3",
                        "account_name": "Wallet",
                        "account_type": "wallet",
                        "currency": "SAR",
                        "opening_balance": 10,
                    },
                }
            ],
        )

        pull = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id))
        self.assertTrue(any(item.get("entity_type") == "Hisabi Account" for item in pull.get("items", [])))
        cursor = pull["next_cursor"]
        self.assertIsInstance(cursor, str)

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-3-del",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-3",
                    "operation": "delete",
                    "base_version": 1,
                    "payload": {
                        "client_id": "acc-3",
                        "client_modified_ms": 1700000000003,
                    },
                }
            ],
        )

        pull_after = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id, cursor=cursor))
        account_changes = [
            row for row in pull_after.get("items", []) if row.get("entity_type") == "Hisabi Account"
        ]
        self.assertTrue(any(row.get("client_id") == "acc-3" for row in account_changes))
        deleted_row = next(row for row in account_changes if row.get("client_id") == "acc-3")
        self.assertEqual(deleted_row.get("is_deleted"), 1)
        self.assertTrue(deleted_row.get("deleted_at"))

    def test_delete_transaction_updates_balance(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-del",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-del",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-del",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 100,
                    },
                },
                {
                    "op_id": "op-tx-del",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-del",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-del",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 30,
                        "currency": "SAR",
                        "account": "acc-del",
                    },
                },
            ],
        )

        acc = frappe.get_doc("Hisabi Account", "acc-del")
        self.assertEqual(acc.current_balance, 70)

        pull_before = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id))
        cursor = pull_before["next_cursor"]

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-del-delete",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-del",
                    "operation": "delete",
                    "base_version": 1,
                    "payload": {
                        "client_id": "tx-del",
                        "client_modified_ms": 1700000004000,
                    },
                }
            ],
        )

        acc.reload()
        self.assertEqual(acc.current_balance, 100)
        tx_name = frappe.get_value("Hisabi Transaction", {"client_id": "tx-del", "wallet_id": self.wallet_id})
        self.assertTrue(tx_name)
        tx = frappe.get_doc("Hisabi Transaction", tx_name)
        self.assertEqual(tx.is_deleted, 1)
        self.assertTrue(tx.deleted_at)

        pull_after = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id, cursor=cursor))
        tx_changes = [
            row for row in pull_after.get("items", []) if row.get("entity_type") == "Hisabi Transaction"
        ]
        self.assertTrue(any(row.get("client_id") == "tx-del" for row in tx_changes))

    def test_update_transaction_recalculates_previous_and_new_accounts(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-a-upd",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-upd-a",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-upd-a",
                        "account_name": "Account A",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 100,
                    },
                },
                {
                    "op_id": "op-acc-b-upd",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-upd-b",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-upd-b",
                        "account_name": "Account B",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 50,
                    },
                },
                {
                    "op_id": "op-tx-upd-account-create",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-upd-account",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-upd-account",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 20,
                        "currency": "SAR",
                        "account": "acc-upd-a",
                    },
                },
            ],
        )

        account_a = frappe.get_doc("Hisabi Account", "acc-upd-a")
        account_b = frappe.get_doc("Hisabi Account", "acc-upd-b")
        tx_name = frappe.get_value("Hisabi Transaction", {"client_id": "tx-upd-account", "wallet_id": self.wallet_id})
        self.assertTrue(tx_name)
        tx = frappe.get_doc("Hisabi Transaction", tx_name)
        self.assertEqual(account_a.current_balance, 80)
        self.assertEqual(account_b.current_balance, 50)
        self.assertEqual(tx.doc_version, 1)

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-upd-account-update",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-upd-account",
                    "operation": "update",
                    "base_version": 1,
                    "payload": {
                        "client_id": "tx-upd-account",
                        "account": "acc-upd-b",
                    },
                }
            ],
        )

        account_a.reload()
        account_b.reload()
        self.assertEqual(account_a.current_balance, 100)
        self.assertEqual(account_b.current_balance, 30)

    def test_duplicate_op_returns_stored_result(self):
        first = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-dup-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-dup",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-dup",
                        "account_name": "Wallet",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 5,
                    },
                }
            ],
        )

        first_result = first["results"][0]

        second = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-dup-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-dup",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-dup",
                        "account_name": "Wallet Updated",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 999,
                    },
                }
            ],
        )

        second_result = second["results"][0]
        self.assertEqual(second_result.get("status"), first_result.get("status"))
        self.assertEqual(second_result.get("op_id"), first_result.get("op_id"))
        self.assertEqual(second_result.get("entity_type"), first_result.get("entity_type"))
        self.assertEqual(second_result.get("client_id"), first_result.get("client_id"))
        self.assertEqual(second_result.get("entity_id"), first_result.get("entity_id"))
        self.assertEqual(second_result.get("doc_version"), first_result.get("doc_version"))
        self.assertEqual(second_result.get("server_modified"), first_result.get("server_modified"))
        self.assertTrue(second_result.get("already_applied"))

    def test_cursor_dt_normalizes_aware_and_naive_for_tuple_compare(self):
        aware = datetime(2026, 2, 8, 12, 30, 45, tzinfo=timezone.utc)
        naive = datetime(2026, 2, 8, 12, 30, 45)
        aware_norm = _cursor_dt(aware)
        naive_norm = _cursor_dt(naive)

        self.assertEqual(aware_norm, naive_norm)
        self.assertIsNone(aware_norm.tzinfo)

        from_cursor = (_cursor_dt("2026-02-08T12:30:45+00:00"), "Hisabi Account", "acc-1")
        from_db = (_cursor_dt("2026-02-08 12:30:45"), "Hisabi Account", "acc-2")
        self.assertTrue(from_db > from_cursor)
