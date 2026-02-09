import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestSyncTransactionBucket(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"sync_tb_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Sync",
                "last_name": "TB",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.suffix = frappe.generate_hash(length=6)

        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        self.wallet_id = ensure_default_wallet_for_user(self.user.name, device_id=self.device_id)
        self.device_token, _device = issue_device_token_for_device(
            user=self.user.name,
            device_id=self.device_id,
            platform="android",
            device_name="Pixel 8",
            wallet_id=self.wallet_id,
        )
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()

    def _push(self, items):
        response = sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(response, "status_code", 200), 200, msg=payload)
            response = payload.get("message", payload)
        return response

    def test_transaction_bucket_create_mirrors_legacy_allocation(self):
        acc_id = f"acc-sync-tb-{self.suffix}"
        bucket_id = f"bucket-sync-tb-{self.suffix}"
        tx_id = f"tx-sync-tb-{self.suffix}"
        row_id = f"tb-row-sync-{self.suffix}"

        account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": acc_id,
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": bucket_id,
                "title": "Primary Bucket",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        tx = frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": tx_id,
                "transaction_type": "income",
                "date_time": now_datetime(),
                "amount": 120,
                "currency": "SAR",
                "account": account.name,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        response = self._push(
            [
                {
                    "op_id": "op-tb-row",
                    "entity_type": "Hisabi Transaction Bucket",
                    "entity_id": row_id,
                    "operation": "create",
                    "payload": {
                        "client_id": row_id,
                        "transaction_id": tx.name,
                        "bucket_id": bucket.name,
                        "amount": 120,
                        "percentage": 100,
                    },
                }
            ]
        )

        result = response["results"][0]
        self.assertEqual(result.get("status"), "accepted")

        tx_bucket = frappe.get_doc("Hisabi Transaction Bucket", row_id)
        self.assertEqual(tx_bucket.transaction_id, tx.name)
        self.assertEqual(tx_bucket.bucket_id, bucket.name)
        self.assertEqual(tx_bucket.amount, 120)
        self.assertEqual(tx_bucket.percentage, 100)

        legacy_name = frappe.get_value(
            "Hisabi Transaction Allocation",
            {"client_id": row_id, "wallet_id": self.wallet_id, "is_deleted": 0},
            "name",
        )
        self.assertTrue(legacy_name)
        legacy_alloc = frappe.get_doc("Hisabi Transaction Allocation", legacy_name)
        self.assertEqual(legacy_alloc.transaction, tx.name)
        self.assertEqual(legacy_alloc.bucket, bucket.name)
        self.assertEqual(legacy_alloc.amount, 120)
        self.assertEqual(legacy_alloc.percent, 100)

    def test_transaction_bucket_rejects_non_income_transaction(self):
        acc_id = f"acc-sync-tb-2-{self.suffix}"
        bucket_id = f"bucket-sync-tb-2-{self.suffix}"
        tx_id = f"tx-sync-tb-2-{self.suffix}"
        row_id = f"tb-row-sync-2-{self.suffix}"

        account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": acc_id,
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": bucket_id,
                "title": "Ops Bucket",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        tx = frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": tx_id,
                "transaction_type": "expense",
                "date_time": now_datetime(),
                "amount": 30,
                "currency": "SAR",
                "account": account.name,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        response = self._push(
            [
                {
                    "op_id": "op-tb-row-2",
                    "entity_type": "Hisabi Transaction Bucket",
                    "entity_id": row_id,
                    "operation": "create",
                    "payload": {
                        "client_id": row_id,
                        "transaction_id": tx.name,
                        "bucket_id": bucket.name,
                        "amount": 30,
                    },
                }
            ]
        )

        result = response["results"][0]
        self.assertEqual(result.get("status"), "rejected")
        self.assertEqual(result.get("error_code"), "rejected")
        self.assertIn("income transactions", (result.get("detail") or "").lower())
        self.assertFalse(frappe.db.exists("Hisabi Transaction Bucket", row_id))
