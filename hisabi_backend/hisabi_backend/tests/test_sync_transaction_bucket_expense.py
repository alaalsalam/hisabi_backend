import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.sync import sync_pull, sync_push
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestSyncTransactionBucketExpense(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"sync_tbe_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Sync",
                "last_name": "TBE",
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
        frappe.local.request = type(
            "obj",
            (object,),
            {
                "headers": {"Authorization": f"Bearer {self.device_token}"},
                "args": {},
                "form_dict": {},
                "query_string": b"",
                "data": b"",
                "content_type": "",
            },
        )()

    def _push(self, items):
        response = sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(response, "status_code", 200), 200, msg=payload)
            response = payload.get("message", payload)
        return response

    def test_sync_roundtrip_transaction_bucket_expense(self):
        account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-sync-tbe-{self.suffix}",
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
                "client_id": f"bucket-sync-tbe-{self.suffix}",
                "title": "Ops Bucket",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        tx = frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": f"tx-sync-tbe-{self.suffix}",
                "transaction_type": "expense",
                "date_time": now_datetime(),
                "amount": 45,
                "currency": "SAR",
                "account": account.name,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        row_id = f"tbe-row-sync-{self.suffix}"
        response = self._push(
            [
                {
                    "op_id": "op-tbe-row",
                    "entity_type": "Hisabi Transaction Bucket Expense",
                    "entity_id": row_id,
                    "operation": "create",
                    "payload": {
                        "client_id": row_id,
                        "transaction_id": tx.name,
                        "bucket_id": bucket.name,
                    },
                }
            ]
        )

        result = response["results"][0]
        self.assertEqual(result.get("status"), "accepted")
        self.assertEqual(result.get("entity_id"), row_id)

        row = frappe.get_doc("Hisabi Transaction Bucket Expense", row_id)
        self.assertEqual(row.transaction_id, tx.name)
        self.assertEqual(row.bucket_id, bucket.name)
        self.assertEqual(row.name, row.client_id)

        pull = sync_pull(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            since="1970-01-01T00:00:00",
            limit=500,
        )
        if hasattr(pull, "get_data"):
            payload = json.loads(pull.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(pull, "status_code", 200), 200, msg=payload)
            pull = payload.get("message", payload)

        items = pull.get("items") or []
        tbe_items = [item for item in items if item.get("entity_type") == "Hisabi Transaction Bucket Expense"]
        self.assertTrue(any((item.get("entity_id") == row_id) for item in tbe_items))
        self.assertTrue(
            any(((item.get("payload") or {}).get("transaction_id") == tx.name) for item in tbe_items)
        )
