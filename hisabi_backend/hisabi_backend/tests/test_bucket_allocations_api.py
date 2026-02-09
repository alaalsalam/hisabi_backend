import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.allocations import set_manual_allocations as set_manual_allocations_api
from hisabi_backend.api.v1.reports import bucket_summary
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBucketAllocationsAPI(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"bucket_api_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Bucket",
                "last_name": "API",
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

        self.account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-bucket-api-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.bucket_a = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-api-a-{self.suffix}",
                "title": "Ops",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket_b = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-api-b-{self.suffix}",
                "title": "Growth",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _create_income_tx(self, client_id: str, amount: float = 100):
        return frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": client_id,
                "transaction_type": "income",
                "date_time": now_datetime(),
                "amount": amount,
                "currency": "SAR",
                "account": self.account.name,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_set_manual_allocations_returns_422_for_invalid_total(self):
        tx = self._create_income_tx(f"tx-bucket-api-1-{self.suffix}", amount=100)

        response = set_manual_allocations_api(
            transaction_id=tx.name,
            mode="amount",
            allocations=[{"bucket": self.bucket_a.name, "value": 60}],
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )

        self.assertEqual(getattr(response, "status_code", None), 422)
        payload = json.loads(response.get_data(as_text=True) or "{}")
        self.assertEqual(payload.get("error", {}).get("code"), "invalid_bucket_allocation")
        self.assertEqual(
            payload.get("error", {}).get("message"),
            "Allocations must sum to transaction value.",
        )

    def test_set_manual_allocations_persists_transaction_buckets(self):
        tx = self._create_income_tx(f"tx-bucket-api-2-{self.suffix}", amount=100)

        response = set_manual_allocations_api(
            transaction_id=tx.name,
            mode="amount",
            allocations=[
                {"bucket": self.bucket_a.name, "value": 40},
                {"bucket": self.bucket_b.name, "value": 60},
            ],
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )

        self.assertEqual(response.get("status"), "ok")
        tx_bucket_rows = frappe.get_all(
            "Hisabi Transaction Bucket",
            filters={"transaction_id": tx.name, "is_deleted": 0},
            fields=["amount", "percentage"],
        )
        self.assertEqual(len(tx_bucket_rows), 2)
        self.assertAlmostEqual(sum(row.amount for row in tx_bucket_rows), 100)

    def test_bucket_summary_reads_transaction_bucket_allocations(self):
        tx = self._create_income_tx(f"tx-bucket-api-3-{self.suffix}", amount=100)

        response = set_manual_allocations_api(
            transaction_id=tx.name,
            mode="amount",
            allocations=[
                {"bucket": self.bucket_a.name, "value": 40},
                {"bucket": self.bucket_b.name, "value": 60},
            ],
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )
        self.assertEqual(response.get("status"), "ok")

        summary = bucket_summary(wallet_id=self.wallet_id, device_id=self.device_id)
        bucket_map = {row.get("bucket"): row for row in summary.get("buckets") or []}
        self.assertAlmostEqual((bucket_map.get(self.bucket_a.name) or {}).get("income_allocated") or 0, 40)
        self.assertAlmostEqual((bucket_map.get(self.bucket_b.name) or {}).get("income_allocated") or 0, 60)
