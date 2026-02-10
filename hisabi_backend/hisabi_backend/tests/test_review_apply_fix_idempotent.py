import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.review import apply_fix as review_apply_fix
from hisabi_backend.api.v1.review import issues as review_issues
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestReviewApplyFixIdempotent(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"review_fix_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Review",
                "last_name": "Fix",
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

        self.account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-review-fix-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.category = frappe.get_doc(
            {
                "doctype": "Hisabi Category",
                "client_id": f"cat-review-fix-{self.suffix}",
                "category_name": "Food",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-review-fix-{self.suffix}",
                "title": "Home",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _create_transaction(self, client_id: str, tx_type: str, amount: float):
        payload = {
            "doctype": "Hisabi Transaction",
            "client_id": client_id,
            "transaction_type": tx_type,
            "date_time": now_datetime(),
            "amount": amount,
            "currency": "SAR",
            "account": self.account.name,
            "user": self.user.name,
            "wallet_id": self.wallet_id,
        }
        if tx_type == "expense":
            payload["category"] = self.category.name
        return frappe.get_doc(payload).insert(ignore_permissions=True)

    def _seed_duplicate_recurring_output(self):
        today = datetime.date.today()
        rule = frappe.get_doc(
            {
                "doctype": "Hisabi Recurring Rule",
                "client_id": f"rrule-review-fix-{self.suffix}",
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "is_active": 1,
                "title": "Daily",
                "transaction_type": "expense",
                "amount": 15,
                "currency": "SAR",
                "category_id": self.category.name,
                "account_id": self.account.name,
                "start_date": today,
                "timezone": "Asia/Aden",
                "rrule_type": "daily",
                "interval": 1,
                "end_mode": "none",
                "created_from": "cloud",
            }
        ).insert(ignore_permissions=True)

        tx_keep = self._create_transaction(f"tx-dup-keep-{self.suffix}", "expense", 15)
        tx_drop = self._create_transaction(f"tx-dup-drop-{self.suffix}", "expense", 15)

        inst_one = frappe.get_doc(
            {
                "doctype": "Hisabi Recurring Instance",
                "client_id": f"rinst-dup-1-{self.suffix}",
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "rule_id": rule.name,
                "occurrence_date": today,
                "transaction_id": tx_keep.name,
                "status": "generated",
                "generated_at": now_datetime(),
            }
        ).insert(ignore_permissions=True)

        inst_two = frappe.get_doc(
            {
                "doctype": "Hisabi Recurring Instance",
                "client_id": f"rinst-dup-2-{self.suffix}",
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "rule_id": rule.name,
                "occurrence_date": today + datetime.timedelta(days=1),
                "transaction_id": tx_drop.name,
                "status": "generated",
                "generated_at": now_datetime(),
            }
        ).insert(ignore_permissions=True)

        # Force duplicate same rule/date by bypassing DocType validation.
        frappe.db.set_value(
            "Hisabi Recurring Instance",
            inst_two.name,
            "occurrence_date",
            today,
            update_modified=False,
        )

        return rule, tx_keep, tx_drop, inst_one, inst_two

    def test_apply_fix_is_idempotent_for_supported_actions(self):
        income_tx = self._create_transaction(f"tx-income-fix-{self.suffix}", "income", 120)
        expense_tx = self._create_transaction(f"tx-expense-fix-{self.suffix}", "expense", 36)
        rule, tx_keep, tx_drop, _inst_one, _inst_two = self._seed_duplicate_recurring_output()

        from_date = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
        to_date = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        issues_payload = review_issues(wallet_id=self.wallet_id, from_date=from_date, to_date=to_date)
        issues = issues_payload.get("issues") or []

        by_type = {row.get("type"): row for row in issues}
        self.assertIn("missing_income_allocation", by_type)
        self.assertIn("missing_expense_bucket", by_type)
        self.assertIn("duplicate_recurring_output", by_type)

        income_issue = by_type["missing_income_allocation"]
        expense_issue = by_type["missing_expense_bucket"]
        duplicate_issue = by_type["duplicate_recurring_output"]
        income_issue_id = income_issue.get("issue_id")
        expense_issue_id = expense_issue.get("issue_id")
        duplicate_issue_id = duplicate_issue.get("issue_id")

        fixes = [
            {
                "issue_id": income_issue_id,
                "action": "open_allocation",
                "payload": {
                    "transaction_id": income_tx.name,
                    "bucket_id": self.bucket.name,
                },
            },
            {
                "issue_id": expense_issue_id,
                "action": "assign_bucket",
                "payload": {
                    "transaction_id": expense_tx.name,
                    "bucket_id": self.bucket.name,
                },
            },
            {
                "issue_id": duplicate_issue_id,
                "action": "dedupe_keep_one",
                "payload": {
                    "rule_id": rule.name,
                    "occurrence_date": datetime.date.today().isoformat(),
                },
            },
        ]

        first = review_apply_fix(wallet_id=self.wallet_id, fixes=fixes)
        self.assertIsInstance(first, dict)
        self.assertEqual(first.get("errors"), [])
        self.assertEqual(int(first.get("applied") or 0), 3)

        assignment = frappe.get_value(
            "Hisabi Transaction Bucket Expense",
            {"wallet_id": self.wallet_id, "transaction_id": expense_tx.name, "is_deleted": 0},
            "bucket_id",
        )
        self.assertEqual(assignment, self.bucket.name)

        active_income_alloc_count = frappe.db.count(
            "Hisabi Transaction Bucket",
            {"wallet_id": self.wallet_id, "transaction_id": income_tx.name, "is_deleted": 0},
        )
        self.assertGreater(active_income_alloc_count, 0)

        self.assertEqual(cint(frappe.get_value("Hisabi Transaction", tx_keep.name, "is_deleted") or 0), 0)
        self.assertEqual(cint(frappe.get_value("Hisabi Transaction", tx_drop.name, "is_deleted") or 0), 1)

        second = review_apply_fix(wallet_id=self.wallet_id, fixes=fixes)
        self.assertEqual(int(second.get("applied") or 0), 0)
        self.assertEqual(second.get("errors"), [])
        skipped = second.get("skipped") or []
        self.assertEqual(len(skipped), 3)
        self.assertTrue(all((row.get("reason") or "").startswith("already_applied") for row in skipped))

        after_issues = review_issues(wallet_id=self.wallet_id, from_date=from_date, to_date=to_date)
        after_issue_ids = {row.get("issue_id") for row in (after_issues.get("issues") or [])}
        self.assertNotIn(income_issue_id, after_issue_ids)
        self.assertNotIn(expense_issue_id, after_issue_ids)
        self.assertNotIn(duplicate_issue_id, after_issue_ids)


def cint(value):
    try:
        return int(value)
    except Exception:
        return 0
