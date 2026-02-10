import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.allocations import set_manual_allocations as set_manual_allocations_api
from hisabi_backend.api.v1.bucket_expenses import set as set_bucket_expense
from hisabi_backend.api.v1.reports_finance import report_bucket_effectiveness
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBucketEffectivenessReport(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"bucket_eff_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Bucket",
                "last_name": "Effect",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123A!")
        self.user = user.name
        self.suffix = frappe.generate_hash(length=6)

        self.device_id = f"device-{self.suffix}"
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

        self.account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-bucket-eff-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-eff-{self.suffix}",
                "title": "Essentials",
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self._ensure_wallet_base_currency("SAR")

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

    def _create_tx(self, *, tx_type: str, amount: float, client_id: str):
        return frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": client_id,
                "transaction_type": tx_type,
                "date_time": now_datetime(),
                "amount": amount,
                "currency": "SAR",
                "account": self.account.name,
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_effectiveness_includes_assigned_and_unallocated_expenses(self):
        income = self._create_tx(tx_type="income", amount=100, client_id=f"tx-inc-eff-{self.suffix}")
        set_result = set_manual_allocations_api(
            transaction_id=income.name,
            mode="amount",
            allocations=[{"bucket": self.bucket.name, "value": 100}],
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )
        self.assertEqual(set_result.get("status"), "ok")

        mapped_expense = self._create_tx(tx_type="expense", amount=30, client_id=f"tx-exp-map-eff-{self.suffix}")
        set_bucket_expense(
            wallet_id=self.wallet_id,
            transaction_id=mapped_expense.name,
            bucket_id=self.bucket.name,
            client_id=f"tbe-eff-{self.suffix}",
            device_id=self.device_id,
        )
        self._create_tx(tx_type="expense", amount=20, client_id=f"tx-exp-unalloc-eff-{self.suffix}")

        payload = report_bucket_effectiveness(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertIsInstance(payload, dict)
        self.assertIn("data", payload)
        self.assertIn("unallocated", payload)
        self.assertEqual(payload.get("currency"), "SAR")

        bucket_map = {row.get("bucket_id"): row for row in payload.get("data") or []}
        row = bucket_map.get(self.bucket.name) or {}
        self.assertAlmostEqual(row.get("income_allocated") or 0, 100, places=2)
        self.assertAlmostEqual(row.get("expenses_assigned") or 0, 30, places=2)
        self.assertAlmostEqual(row.get("net") or 0, 70, places=2)
        self.assertAlmostEqual(row.get("savings_delta") or 0, 70, places=2)

        unallocated = payload.get("unallocated") or {}
        self.assertEqual(unallocated.get("bucket_id"), "unallocated")
        self.assertAlmostEqual(unallocated.get("expenses_assigned") or 0, 20, places=2)
        self.assertAlmostEqual(unallocated.get("net") or 0, -20, places=2)
