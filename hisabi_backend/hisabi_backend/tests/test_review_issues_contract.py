import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.review import issues as review_issues
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestReviewIssuesContract(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"review_contract_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Review",
                "last_name": "Contract",
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

        self.account_sar = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-review-sar-{self.suffix}",
                "account_name": "Cash SAR",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.account_zzz = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-review-zzz-{self.suffix}",
                "account_name": "Cash ZZZ",
                "account_type": "cash",
                "currency": "ZZZ",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.category = frappe.get_doc(
            {
                "doctype": "Hisabi Category",
                "client_id": f"cat-review-{self.suffix}",
                "category_name": "Food",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-review-{self.suffix}",
                "title": "Essentials",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _create_transaction(self, client_id: str, tx_type: str, amount: float, currency: str, account: str):
        payload = {
            "doctype": "Hisabi Transaction",
            "client_id": client_id,
            "transaction_type": tx_type,
            "date_time": now_datetime(),
            "amount": amount,
            "currency": currency,
            "account": account,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }
        if tx_type == "expense":
            payload["category"] = self.category.name
        return frappe.get_doc(payload).insert(ignore_permissions=True)

    def _create_orphan_generated_instance(self):
        rule = frappe.get_doc(
            {
                "doctype": "Hisabi Recurring Rule",
                "client_id": f"rrule-review-{self.suffix}",
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "is_active": 1,
                "title": "Lunch",
                "transaction_type": "expense",
                "amount": 14,
                "currency": "SAR",
                "category_id": self.category.name,
                "account_id": self.account_sar.name,
                "start_date": datetime.date.today(),
                "timezone": "Asia/Aden",
                "rrule_type": "daily",
                "interval": 1,
                "end_mode": "none",
                "created_from": "cloud",
            }
        ).insert(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "Hisabi Recurring Instance",
                "client_id": f"rinst-review-{self.suffix}",
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "rule_id": rule.name,
                "occurrence_date": datetime.date.today(),
                "status": "generated",
                "generated_at": now_datetime(),
            }
        ).insert(ignore_permissions=True)

    def test_review_issues_contract_and_required_types(self):
        self._create_transaction(f"tx-income-review-{self.suffix}", "income", 100, "SAR", self.account_sar.name)
        self._create_transaction(f"tx-expense-review-{self.suffix}", "expense", 45, "SAR", self.account_sar.name)
        self._create_transaction(f"tx-fx-review-{self.suffix}", "expense", 12, "ZZZ", self.account_zzz.name)
        self._create_orphan_generated_instance()

        from_date = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
        to_date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

        result = review_issues(wallet_id=self.wallet_id, from_date=from_date, to_date=to_date)

        self.assertIsInstance(result, dict)
        meta = result.get("meta") or {}
        self.assertEqual(meta.get("wallet_id"), self.wallet_id)
        self.assertEqual(meta.get("from_date"), from_date)
        self.assertEqual(meta.get("to_date"), to_date)
        self.assertTrue(meta.get("generated_at"))
        self.assertTrue(meta.get("server_time"))
        self.assertTrue(meta.get("version"))
        self.assertTrue(meta.get("commit"))

        issues = result.get("issues") or []
        self.assertTrue(issues)

        issue_types = {row.get("type") for row in issues}
        self.assertIn("missing_income_allocation", issue_types)
        self.assertIn("missing_expense_bucket", issue_types)
        self.assertIn("orphan_recurring_instance", issue_types)
        self.assertIn("fx_missing", issue_types)

        for row in issues:
            self.assertTrue(str(row.get("issue_id") or "").startswith("ISSUE-"))
            self.assertIn(row.get("severity"), {"high", "medium", "low"})
            entity = row.get("entity") or {}
            self.assertTrue(entity.get("doctype"))
            self.assertTrue(entity.get("id"))
            self.assertIsInstance(row.get("suggested_actions") or [], list)

        stats = result.get("stats") or {}
        self.assertEqual(stats.get("total"), len(issues))
        self.assertEqual(
            stats.get("total"),
            int(stats.get("high") or 0) + int(stats.get("medium") or 0) + int(stats.get("low") or 0),
        )
