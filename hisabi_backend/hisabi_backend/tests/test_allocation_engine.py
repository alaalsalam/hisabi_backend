import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.domain.allocation_engine import apply_auto_allocations
from hisabi_backend.install import ensure_roles
from hisabi_backend.api.v1 import wallet_create


class TestAllocationEngine(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"alloc_test_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Alloc",
            "last_name": "Tester",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Test Wallet")

        self.account = frappe.get_doc({
            "doctype": "Hisabi Account",
            "client_id": "acc-alloc",
            "account_name": "Cash",
            "account_type": "cash",
            "currency": "SAR",
            "opening_balance": 0,
            "user": user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        self.bucket_a = frappe.get_doc({
            "doctype": "Hisabi Bucket",
            "client_id": "bucket-a",
            "bucket_name": "Personal",
            "user": user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        self.bucket_b = frappe.get_doc({
            "doctype": "Hisabi Bucket",
            "client_id": "bucket-b",
            "bucket_name": "Savings",
            "user": user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        self.rule = frappe.get_doc({
            "doctype": "Hisabi Allocation Rule",
            "client_id": "rule-1",
            "rule_name": "Default",
            "scope_type": "global",
            "is_default": 1,
            "active": 1,
            "user": user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        frappe.get_doc({
            "doctype": "Hisabi Allocation Rule Line",
            "client_id": "line-1",
            "rule": self.rule.name,
            "bucket": self.bucket_a.name,
            "percent": 50,
            "user": user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        frappe.get_doc({
            "doctype": "Hisabi Allocation Rule Line",
            "client_id": "line-2",
            "rule": self.rule.name,
            "bucket": self.bucket_b.name,
            "percent": 50,
            "user": user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

    def test_income_allocations_created(self):
        tx = frappe.get_doc({
            "doctype": "Hisabi Transaction",
            "client_id": "tx-income-1",
            "transaction_type": "income",
            "date_time": now_datetime(),
            "amount": 101,
            "currency": "SAR",
            "account": self.account.name,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        allocs = frappe.get_all(
            "Hisabi Transaction Allocation",
            filters={"transaction": tx.name, "is_manual_override": 0},
            fields=["amount"],
        )
        self.assertEqual(len(allocs), 2)
        self.assertAlmostEqual(sum([a.amount for a in allocs]), 101)

    def test_update_income_amount_updates_allocations(self):
        tx = frappe.get_doc({
            "doctype": "Hisabi Transaction",
            "client_id": "tx-income-2",
            "transaction_type": "income",
            "date_time": now_datetime(),
            "amount": 100,
            "currency": "SAR",
            "account": self.account.name,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        tx.amount = 120
        tx.save(ignore_permissions=True)

        allocs = frappe.get_all(
            "Hisabi Transaction Allocation",
            filters={"transaction": tx.name, "is_manual_override": 0},
            fields=["amount"],
        )
        self.assertAlmostEqual(sum([a.amount for a in allocs]), 120)

    def test_manual_allocations_override(self):
        tx = frappe.get_doc({
            "doctype": "Hisabi Transaction",
            "client_id": "tx-income-3",
            "transaction_type": "income",
            "date_time": now_datetime(),
            "amount": 100,
            "currency": "SAR",
            "account": self.account.name,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        frappe.get_doc({
            "doctype": "Hisabi Transaction Allocation",
            "client_id": "manual-1",
            "transaction": tx.name,
            "bucket": self.bucket_a.name,
            "percent": 100,
            "amount": 100,
            "currency": "SAR",
            "amount_base": 100,
            "is_manual_override": 1,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        tx.amount = 200
        tx.save(ignore_permissions=True)

        allocs = frappe.get_all(
            "Hisabi Transaction Allocation",
            filters={"transaction": tx.name, "is_manual_override": 1},
            fields=["amount"],
        )
        self.assertEqual(len(allocs), 1)
        self.assertAlmostEqual(allocs[0].amount, 100)

    def test_delete_income_transaction_cleans_allocations(self):
        tx = frappe.get_doc({
            "doctype": "Hisabi Transaction",
            "client_id": "tx-income-4",
            "transaction_type": "income",
            "date_time": now_datetime(),
            "amount": 100,
            "currency": "SAR",
            "account": self.account.name,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        tx.is_deleted = 1
        tx.save(ignore_permissions=True)

        allocs = frappe.get_all(
            "Hisabi Transaction Allocation",
            filters={"transaction": tx.name},
            fields=["name"],
        )
        self.assertEqual(len(allocs), 0)

    def test_bucket_summary(self):
        tx = frappe.get_doc({
            "doctype": "Hisabi Transaction",
            "client_id": "tx-income-5",
            "transaction_type": "income",
            "date_time": now_datetime(),
            "amount": 100,
            "currency": "SAR",
            "account": self.account.name,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }).insert(ignore_permissions=True)

        apply_auto_allocations(tx)

        from hisabi_backend.api.v1.reports import bucket_summary
        summary = bucket_summary(wallet_id=self.wallet_id)
        buckets = {row["bucket"]: row for row in summary["buckets"]}
        self.assertEqual(buckets[self.bucket_a.name]["income_allocated"], 50)
        self.assertEqual(buckets[self.bucket_b.name]["income_allocated"], 50)
