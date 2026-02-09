import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.allocations import set_manual_allocations as set_manual_allocations_api
from hisabi_backend.api.v1.reports_finance import report_bucket_breakdown
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBucketBreakdownReport(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"bucket_breakdown_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Bucket",
                "last_name": "Breakdown",
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
                "client_id": f"acc-bucket-breakdown-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket_a = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-a-breakdown-{self.suffix}",
                "title": "Essentials",
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket_b = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-b-breakdown-{self.suffix}",
                "title": "Savings",
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

    def _create_income_tx(self, *, amount: float, currency: str = "SAR", client_id: str | None = None):
        return frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": client_id or f"tx-breakdown-{frappe.generate_hash(length=6)}",
                "transaction_type": "income",
                "date_time": now_datetime(),
                "amount": amount,
                "currency": currency,
                "account": self.account.name,
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _set_manual_allocations(self, tx_name: str, rows: list[dict]):
        response = set_manual_allocations_api(
            transaction_id=tx_name,
            mode="amount",
            allocations=rows,
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )
        self.assertEqual(response.get("status"), "ok")

    def _bucket_map(self):
        payload = report_bucket_breakdown(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertIsInstance(payload, dict)
        return payload, {row.get("bucket_id"): row for row in payload.get("data") or []}

    def test_single_bucket_breakdown(self):
        tx = self._create_income_tx(amount=100)
        self._set_manual_allocations(tx.name, [{"bucket": self.bucket_a.name, "value": 100}])

        payload, bucket_map = self._bucket_map()
        row = bucket_map.get(self.bucket_a.name) or {}
        self.assertAlmostEqual(row.get("total_amount") or 0, 100, places=2)
        self.assertAlmostEqual(row.get("percentage_of_income") or 0, 100, places=2)
        self.assertEqual(payload.get("warnings"), [])

    def test_multi_bucket_breakdown(self):
        tx_one = self._create_income_tx(amount=100)
        self._set_manual_allocations(
            tx_one.name,
            [
                {"bucket": self.bucket_a.name, "value": 40},
                {"bucket": self.bucket_b.name, "value": 60},
            ],
        )
        tx_two = self._create_income_tx(amount=50)
        self._set_manual_allocations(tx_two.name, [{"bucket": self.bucket_a.name, "value": 50}])

        _payload, bucket_map = self._bucket_map()
        self.assertAlmostEqual((bucket_map.get(self.bucket_a.name) or {}).get("total_amount") or 0, 90, places=2)
        self.assertAlmostEqual((bucket_map.get(self.bucket_b.name) or {}).get("total_amount") or 0, 60, places=2)
        self.assertAlmostEqual(
            (bucket_map.get(self.bucket_a.name) or {}).get("percentage_of_income") or 0,
            60,
            places=2,
        )
        self.assertAlmostEqual(
            (bucket_map.get(self.bucket_b.name) or {}).get("percentage_of_income") or 0,
            40,
            places=2,
        )

    def test_legacy_fallback_when_transaction_bucket_rows_missing(self):
        tx = self._create_income_tx(amount=80)
        frappe.get_doc(
            {
                "doctype": "Hisabi Transaction Allocation",
                "client_id": f"legacy-breakdown-{self.suffix}",
                "transaction": tx.name,
                "bucket": self.bucket_a.name,
                "amount": 80,
                "currency": "SAR",
                "amount_base": 80,
                "percent": 100,
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.assertFalse(
            frappe.db.exists(
                "Hisabi Transaction Bucket",
                {"transaction_id": tx.name, "wallet_id": self.wallet_id, "is_deleted": 0},
            )
        )

        _payload, bucket_map = self._bucket_map()
        self.assertAlmostEqual((bucket_map.get(self.bucket_a.name) or {}).get("total_amount") or 0, 80, places=2)

    def test_soft_deleted_bucket_is_excluded(self):
        tx = self._create_income_tx(amount=30)
        self._set_manual_allocations(tx.name, [{"bucket": self.bucket_b.name, "value": 30}])

        self.bucket_b.is_deleted = 1
        self.bucket_b.deleted_at = now_datetime()
        self.bucket_b.save(ignore_permissions=True)

        _payload, bucket_map = self._bucket_map()
        self.assertNotIn(self.bucket_b.name, bucket_map)

    def test_fx_missing_warning_excludes_unconvertible_allocations(self):
        tx_sar = self._create_income_tx(amount=20, currency="SAR")
        self._set_manual_allocations(tx_sar.name, [{"bucket": self.bucket_a.name, "value": 20}])

        tx_usd = self._create_income_tx(amount=10, currency="USD")
        self._set_manual_allocations(tx_usd.name, [{"bucket": self.bucket_a.name, "value": 10}])

        payload, bucket_map = self._bucket_map()
        row = bucket_map.get(self.bucket_a.name) or {}
        self.assertAlmostEqual(row.get("total_amount") or 0, 20, places=2)
        warnings = payload.get("warnings") or []
        self.assertTrue(any(w.get("code") == "fx_missing" for w in warnings))
        self.assertTrue(
            any((w.get("message") or "") == "Some amounts are excluded due to missing FX rates." for w in warnings)
        )
