import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.allocations import set_manual_allocations as set_manual_allocations_api
from hisabi_backend.api.v1.reports_finance import report_cashflow_by_bucket
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBucketCashflowReport(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"bucket_cashflow_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Bucket",
                "last_name": "Cashflow",
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
                "client_id": f"acc-bucket-cashflow-{self.suffix}",
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
                "client_id": f"bucket-a-cashflow-{self.suffix}",
                "title": "Essentials",
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket_b = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-b-cashflow-{self.suffix}",
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

    def _create_transaction(
        self,
        *,
        tx_type: str,
        amount: float,
        currency: str = "SAR",
        client_id: str | None = None,
    ):
        return frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": client_id or f"tx-cashflow-{frappe.generate_hash(length=6)}",
                "transaction_type": tx_type,
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

    def _cashflow_map(self):
        payload = report_cashflow_by_bucket(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertIsInstance(payload, dict)
        data = payload.get("data") or []
        key_map = {(row.get("date"), row.get("bucket_id")): row for row in data}
        return payload, key_map

    def test_single_bucket_income_and_expense_unallocated(self):
        income = self._create_transaction(tx_type="income", amount=100)
        self._set_manual_allocations(income.name, [{"bucket": self.bucket_a.name, "value": 100}])
        expense = self._create_transaction(tx_type="expense", amount=25)

        payload, key_map = self._cashflow_map()
        day = income.date_time.date().isoformat()
        self.assertAlmostEqual((key_map.get((day, self.bucket_a.name)) or {}).get("amount") or 0, 100, places=2)
        self.assertAlmostEqual((key_map.get((day, "unallocated")) or {}).get("amount") or 0, -25, places=2)
        self.assertEqual(payload.get("warnings"), [])
        self.assertEqual(expense.transaction_type, "expense")

    def test_multi_bucket_split_cashflow(self):
        income = self._create_transaction(tx_type="income", amount=100)
        self._set_manual_allocations(
            income.name,
            [
                {"bucket": self.bucket_a.name, "value": 40},
                {"bucket": self.bucket_b.name, "value": 60},
            ],
        )

        _payload, key_map = self._cashflow_map()
        day = income.date_time.date().isoformat()
        self.assertAlmostEqual((key_map.get((day, self.bucket_a.name)) or {}).get("amount") or 0, 40, places=2)
        self.assertAlmostEqual((key_map.get((day, self.bucket_b.name)) or {}).get("amount") or 0, 60, places=2)

    def test_legacy_fallback_when_new_bucket_rows_are_absent(self):
        income = self._create_transaction(tx_type="income", amount=70)
        frappe.get_doc(
            {
                "doctype": "Hisabi Transaction Allocation",
                "client_id": f"legacy-cashflow-{self.suffix}",
                "transaction": income.name,
                "bucket": self.bucket_b.name,
                "amount": 70,
                "currency": "SAR",
                "amount_base": 70,
                "percent": 100,
                "user": self.user,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.assertFalse(
            frappe.db.exists(
                "Hisabi Transaction Bucket",
                {"transaction_id": income.name, "wallet_id": self.wallet_id, "is_deleted": 0},
            )
        )

        _payload, key_map = self._cashflow_map()
        day = income.date_time.date().isoformat()
        self.assertAlmostEqual((key_map.get((day, self.bucket_b.name)) or {}).get("amount") or 0, 70, places=2)

    def test_soft_deleted_bucket_allocations_are_excluded(self):
        income = self._create_transaction(tx_type="income", amount=50)
        self._set_manual_allocations(income.name, [{"bucket": self.bucket_a.name, "value": 50}])

        self.bucket_a.is_deleted = 1
        self.bucket_a.deleted_at = now_datetime()
        self.bucket_a.save(ignore_permissions=True)

        _payload, key_map = self._cashflow_map()
        day = income.date_time.date().isoformat()
        self.assertNotIn((day, self.bucket_a.name), key_map)

    def test_fx_missing_warning_excludes_rows_without_rate(self):
        income = self._create_transaction(tx_type="income", amount=10, currency="USD")
        self._set_manual_allocations(income.name, [{"bucket": self.bucket_a.name, "value": 10}])
        self._create_transaction(tx_type="expense", amount=5, currency="USD")

        payload, key_map = self._cashflow_map()
        self.assertEqual(key_map, {})
        warnings = payload.get("warnings") or []
        self.assertTrue(any(w.get("code") == "fx_missing" for w in warnings))
        self.assertTrue(
            any((w.get("message") or "") == "Some amounts are excluded due to missing FX rates." for w in warnings)
        )
