import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.sync import sync_pull as _sync_pull
from hisabi_backend.api.v1.sync import sync_push as _sync_push
from hisabi_backend.install import ensure_roles


def _sync_push_message(*args, **kwargs):
    response = _sync_push(*args, **kwargs)
    if isinstance(response, dict):
        return response.get("message", response)
    if hasattr(response, "get_data"):
        payload = json.loads(response.get_data(as_text=True) or "{}")
        return payload.get("message", payload)
    return response


def _sync_pull_message(*args, **kwargs):
    response = _sync_pull(*args, **kwargs)
    if isinstance(response, dict):
        return response.get("message", response)
    if hasattr(response, "get_data"):
        payload = json.loads(response.get_data(as_text=True) or "{}")
        return payload.get("message", payload)
    return response


class TestAccountsMultiCurrency(FrappeTestCase):
    def setUp(self):
        self._ensure_account_multicurrency_fields()
        ensure_roles()
        email = f"accounts_test_{frappe.generate_hash(length=8)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Accounts",
                "last_name": "Tester",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user

        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(self.device_id, "android", "Pixel")
        self.device_token = device.get("device_token")
        request_stub = type(
            "obj",
            (object,),
            {
                "headers": {"Authorization": f"Bearer {self.device_token}"},
                "form": {},
                "args": {},
                "data": b"",
                "get_json": lambda self, silent=True: {},
            },
        )()
        frappe.local.request = request_stub
        frappe.request = request_stub

        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Accounts Wallet", device_id=self.device_id)

        self._upsert_wallet_settings(base_currency="SAR")

    def _ensure_account_multicurrency_fields(self):
        custom_fields = [
            {
                "fieldname": "is_multi_currency",
                "label": "Is Multi Currency",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "currency",
            },
            {
                "fieldname": "base_currency",
                "label": "Base Currency",
                "fieldtype": "Data",
                "insert_after": "is_multi_currency",
            },
            {
                "fieldname": "group_id",
                "label": "Group ID",
                "fieldtype": "Data",
                "insert_after": "base_currency",
            },
            {
                "fieldname": "parent_account",
                "label": "Parent Account",
                "fieldtype": "Link",
                "options": "Hisabi Account",
                "insert_after": "group_id",
            },
        ]
        meta = frappe.get_meta("Hisabi Account")
        for custom_field in custom_fields:
            if meta.has_field(custom_field["fieldname"]):
                continue
            create_custom_field("Hisabi Account", custom_field, ignore_validate=True)
        frappe.clear_cache(doctype="Hisabi Account")

    def _upsert_wallet_settings(self, base_currency: str = "SAR"):
        name = frappe.get_value("Hisabi Settings", {"wallet_id": self.wallet_id})
        if name:
            doc = frappe.get_doc("Hisabi Settings", name)
        else:
            doc = frappe.new_doc("Hisabi Settings")
            doc.client_id = f"settings-{self.wallet_id}"
            doc.wallet_id = self.wallet_id
            doc.user = self.user.name
        doc.base_currency = base_currency
        doc.enabled_currencies = json.dumps(["SAR", "USD", "EUR"])
        doc.locale = "ar"
        doc.save(ignore_permissions=True)

    def _create_multi_parent(self, *, client_id: str = "acc-multi-main", base_currency: str = "SAR"):
        response = _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": f"op-create-{client_id}",
                    "entity_type": "Hisabi Account",
                    "entity_id": client_id,
                    "operation": "create",
                    "payload": {
                        "client_id": client_id,
                        "account_name": "Multi Wallet",
                        "account_type": "cash",
                        "currency": base_currency,
                        "base_currency": base_currency,
                        "is_multi_currency": 1,
                        "group_id": f"group-{client_id}",
                        "opening_balance": 0,
                        "client_modified_ms": 1700000000000,
                    },
                }
            ],
        )
        self.assertEqual(response["results"][0]["status"], "accepted", msg=json.dumps(response, default=str))
        parent = frappe.get_doc("Hisabi Account", client_id)
        return parent

    def _upsert_fx_rate(self, source_currency: str, target_currency: str, rate: float):
        fx_name = frappe.get_value(
            "Hisabi FX Rate",
            {
                "wallet_id": self.wallet_id,
                "base_currency": source_currency,
                "quote_currency": target_currency,
                "is_deleted": 0,
            },
        )
        if fx_name:
            fx = frappe.get_doc("Hisabi FX Rate", fx_name)
        else:
            fx = frappe.new_doc("Hisabi FX Rate")
            fx.client_id = f"fx-{source_currency.lower()}-{target_currency.lower()}-{frappe.generate_hash(length=6)}"
            fx.wallet_id = self.wallet_id
            fx.user = self.user.name
        fx.base_currency = source_currency
        fx.quote_currency = target_currency
        fx.rate = rate
        fx.effective_date = now_datetime()
        fx.source = "custom"
        fx.save(ignore_permissions=True)

    def test_create_multi_currency_account_creates_parent_and_base_child(self):
        parent = self._create_multi_parent(client_id="acc-multi-create", base_currency="SAR")
        self.assertEqual(int(parent.is_multi_currency), 1)
        self.assertEqual(parent.base_currency, "SAR")
        self.assertTrue(parent.group_id)

        children = frappe.get_all(
            "Hisabi Account",
            filters={
                "wallet_id": self.wallet_id,
                "parent_account": parent.name,
                "is_deleted": 0,
            },
            fields=["name", "currency", "group_id", "is_multi_currency"],
        )
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["currency"], "SAR")
        self.assertEqual(children[0]["group_id"], parent.group_id)
        self.assertEqual(int(children[0]["is_multi_currency"] or 0), 0)

    def test_transactions_route_to_child_and_recalculate_parent_total(self):
        parent = self._create_multi_parent(client_id="acc-multi-route", base_currency="SAR")
        self._upsert_fx_rate("USD", "SAR", 3.75)

        response = _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-usd-route",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-usd-route",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-usd-route",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 10,
                        "currency": "USD",
                        "account": parent.client_id,
                        "client_modified_ms": 1700000001111,
                    },
                }
            ],
        )
        self.assertEqual(response["results"][0]["status"], "accepted")

        tx_name = frappe.get_value("Hisabi Transaction", {"client_id": "tx-usd-route", "wallet_id": self.wallet_id})
        self.assertTrue(tx_name)
        tx = frappe.get_doc("Hisabi Transaction", tx_name)
        tx_account = frappe.get_doc("Hisabi Account", tx.account)
        parent.reload()

        self.assertEqual(tx_account.parent_account, parent.name)
        self.assertEqual(tx_account.currency, "USD")
        self.assertGreater(flt(tx.fx_rate_used or 0), 0)
        self.assertAlmostEqual(flt(tx.amount_base or 0), 37.5, places=2)
        self.assertAlmostEqual(flt(parent.current_balance or 0), -37.5, places=2)

    def test_cannot_change_base_currency_when_balance_non_zero(self):
        parent = self._create_multi_parent(client_id="acc-multi-guard", base_currency="SAR")

        _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-income-balance",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-income-balance",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-income-balance",
                        "transaction_type": "income",
                        "date_time": now_datetime(),
                        "amount": 100,
                        "currency": "SAR",
                        "account": parent.client_id,
                    },
                }
            ],
        )
        parent.reload()
        self.assertGreater(flt(parent.current_balance or 0), 0)

        reject = _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-update-base-reject",
                    "entity_type": "Hisabi Account",
                    "entity_id": parent.client_id,
                    "operation": "update",
                    "base_version": int(parent.doc_version or 0),
                    "payload": {
                        "client_id": parent.client_id,
                        "base_currency": "USD",
                    },
                }
            ],
        )
        self.assertIn(reject["results"][0]["status"], {"rejected", "error"})

        _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-expense-zero",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-expense-zero",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-expense-zero",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 100,
                        "currency": "SAR",
                        "account": parent.client_id,
                    },
                }
            ],
        )
        parent.reload()
        self.assertAlmostEqual(flt(parent.current_balance or 0), 0, places=2)

        accept = _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-update-base-accept",
                    "entity_type": "Hisabi Account",
                    "entity_id": parent.client_id,
                    "operation": "update",
                    "base_version": int(parent.doc_version or 0),
                    "payload": {
                        "client_id": parent.client_id,
                        "base_currency": "USD",
                    },
                }
            ],
        )
        self.assertEqual(accept["results"][0]["status"], "accepted")

    def test_sync_pull_returns_multi_currency_structure(self):
        parent = self._create_multi_parent(client_id="acc-multi-pull", base_currency="SAR")
        self._upsert_fx_rate("USD", "SAR", 3.75)
        _sync_push_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-pull-usd",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-pull-usd",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-pull-usd",
                        "transaction_type": "income",
                        "date_time": now_datetime(),
                        "amount": 20,
                        "currency": "USD",
                        "account": parent.client_id,
                    },
                }
            ],
        )

        pulled = _sync_pull_message(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            cursor=None,
            limit=500,
        )
        items = pulled.get("items", [])
        parent_item = next(
            (
                row
                for row in items
                if row.get("entity_type") == "Hisabi Account"
                and row.get("client_id") == parent.client_id
            ),
            None,
        )
        self.assertIsNotNone(parent_item)
        payload = parent_item["payload"]
        self.assertEqual(payload.get("base_currency"), "SAR")
        self.assertIsInstance(payload.get("supported_currencies"), list)
        self.assertIn("USD", payload.get("supported_currencies"))
        self.assertIsInstance(payload.get("sub_balances"), list)
        self.assertIsNotNone(payload.get("total_balance_base"))
        self.assertNotIn("password", payload)
