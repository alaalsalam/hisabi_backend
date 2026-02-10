import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.bucket_expenses import clear as clear_bucket_expense
from hisabi_backend.api.v1.bucket_expenses import set as set_bucket_expense
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBucketExpensesAPI(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"bucket_exp_api_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Bucket",
                "last_name": "Expense",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123A!")
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
                "client_id": f"acc-bucket-exp-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-bucket-exp-{self.suffix}",
                "title": "Ops",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.expense_tx = frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": f"tx-exp-bucket-exp-{self.suffix}",
                "transaction_type": "expense",
                "date_time": now_datetime(),
                "amount": 35,
                "currency": "SAR",
                "account": self.account.name,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_set_and_clear_expense_bucket_assignment(self):
        assignment_id = f"tbexp-api-{self.suffix}"
        set_result = set_bucket_expense(
            wallet_id=self.wallet_id,
            transaction_id=self.expense_tx.name,
            bucket_id=self.bucket.name,
            client_id=assignment_id,
            op_id=f"op-set-{self.suffix}",
            device_id=self.device_id,
        )
        self.assertEqual(set_result.get("status"), "ok")
        self.assertEqual((set_result.get("assignment") or {}).get("client_id"), assignment_id)
        self.assertEqual((set_result.get("assignment") or {}).get("bucket_id"), self.bucket.name)

        row = frappe.get_doc("Hisabi Transaction Bucket Expense", assignment_id)
        self.assertEqual(row.transaction_id, self.expense_tx.name)
        self.assertEqual(row.bucket_id, self.bucket.name)
        self.assertEqual(int(row.is_deleted or 0), 0)

        clear_result = clear_bucket_expense(
            wallet_id=self.wallet_id,
            transaction_id=self.expense_tx.name,
            op_id=f"op-clear-{self.suffix}",
            base_version=row.doc_version,
            device_id=self.device_id,
        )
        self.assertEqual(clear_result.get("status"), "ok")
        self.assertTrue(clear_result.get("cleared"))

        cleared = frappe.get_doc("Hisabi Transaction Bucket Expense", assignment_id)
        self.assertEqual(int(cleared.is_deleted or 0), 1)
        self.assertTrue(cleared.deleted_at)

    def test_set_rejects_non_expense_transaction(self):
        income_tx = frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": f"tx-income-bucket-exp-{self.suffix}",
                "transaction_type": "income",
                "date_time": now_datetime(),
                "amount": 50,
                "currency": "SAR",
                "account": self.account.name,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        response = set_bucket_expense(
            wallet_id=self.wallet_id,
            transaction_id=income_tx.name,
            bucket_id=self.bucket.name,
            device_id=self.device_id,
        )
        self.assertEqual(getattr(response, "status_code", None), 422)
        payload = json.loads(response.get_data(as_text=True) or "{}")
        self.assertEqual(payload.get("error", {}).get("code"), "invalid_bucket_expense_assignment")
        self.assertIn("expense", (payload.get("error", {}).get("message") or "").lower())

    def test_set_rejects_transaction_wallet_mismatch(self):
        other_wallet_id = f"wallet-bucket-exp-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=other_wallet_id, wallet_name="Other Wallet", device_id=self.device_id)
        other_bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-other-{self.suffix}",
                "title": "Other Bucket",
                "user": self.user.name,
                "wallet_id": other_wallet_id,
            }
        ).insert(ignore_permissions=True)

        response = set_bucket_expense(
            wallet_id=other_wallet_id,
            transaction_id=self.expense_tx.name,
            bucket_id=other_bucket.name,
            device_id=self.device_id,
        )
        self.assertEqual(getattr(response, "status_code", None), 422)
        payload = json.loads(response.get_data(as_text=True) or "{}")
        self.assertEqual(payload.get("error", {}).get("code"), "invalid_bucket_expense_assignment")
        self.assertIn("wallet", (payload.get("error", {}).get("message") or "").lower())
