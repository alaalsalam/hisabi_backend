import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.sync import sync_pull, sync_push
from hisabi_backend.install import ensure_roles


class TestSyncV1(FrappeTestCase):
    def _pull_message(self, response):
        if isinstance(response, dict):
            return response
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            return payload.get("message", payload)
        return response

    def setUp(self):
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
        self.device_token = device.get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Test Wallet", device_id=self.device_id)

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

        tx = frappe.get_doc("Hisabi Transaction", "tx-cat")
        self.assertEqual(tx.category, "cat-1")

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
        tx = frappe.get_doc("Hisabi Transaction", "tx-del")
        self.assertEqual(tx.is_deleted, 1)
        self.assertTrue(tx.deleted_at)

        pull_after = self._pull_message(sync_pull(device_id=self.device_id, wallet_id=self.wallet_id, cursor=cursor))
        tx_changes = [
            row for row in pull_after.get("items", []) if row.get("entity_type") == "Hisabi Transaction"
        ]
        self.assertTrue(any(row.get("client_id") == "tx-del" for row in tx_changes))

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

        self.assertEqual(second["results"][0], first_result)
