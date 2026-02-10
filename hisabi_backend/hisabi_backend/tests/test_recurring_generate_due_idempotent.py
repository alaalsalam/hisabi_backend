import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.recurring import generate_due, upsert_rule
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestRecurringGenerateDueIdempotent(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"recurring_gen_due_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Recurring",
                "last_name": "GenerateDue",
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
                "client_id": f"acc-rec-gen-due-{self.suffix}",
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
                "client_id": f"cat-rec-gen-due-{self.suffix}",
                "category_name": "Food",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_generate_due_create_missing_is_idempotent(self):
        today = datetime.date.today()
        end_day = today + datetime.timedelta(days=3)
        rule_id = f"rrule-gendue-{self.suffix}"
        upsert_rule(
            wallet_id=self.wallet_id,
            client_id=rule_id,
            title="Daily Lunch",
            transaction_type="expense",
            amount=18,
            currency="SAR",
            category_id=self.category.name,
            account_id=self.account.name,
            start_date=today.isoformat(),
            rrule_type="daily",
            interval=1,
            end_mode="none",
            is_active=1,
        )

        first = generate_due(
            wallet_id=self.wallet_id,
            from_date=today.isoformat(),
            to_date=end_day.isoformat(),
            mode="create_missing",
            device_id=self.device_id,
        )
        self.assertIsInstance(first, dict)
        self.assertIn("created", first)
        self.assertIn("skipped", first)
        self.assertIn("conflicts", first)
        self.assertEqual(first.get("conflicts"), [])
        self.assertGreater(int((first.get("created") or {}).get("transactions") or 0), 0)
        self.assertGreater(int((first.get("created") or {}).get("instances") or 0), 0)

        tx_count_after_first = frappe.db.count("Hisabi Transaction", {"wallet_id": self.wallet_id, "is_deleted": 0})
        instance_count_after_first = frappe.db.count(
            "Hisabi Recurring Instance", {"wallet_id": self.wallet_id, "is_deleted": 0}
        )

        second = generate_due(
            wallet_id=self.wallet_id,
            from_date=today.isoformat(),
            to_date=end_day.isoformat(),
            mode="create_missing",
            device_id=self.device_id,
        )
        self.assertEqual(int((second.get("created") or {}).get("transactions") or 0), 0)
        self.assertEqual(int((second.get("created") or {}).get("instances") or 0), 0)
        self.assertEqual(tx_count_after_first, frappe.db.count("Hisabi Transaction", {"wallet_id": self.wallet_id, "is_deleted": 0}))
        self.assertEqual(
            instance_count_after_first,
            frappe.db.count("Hisabi Recurring Instance", {"wallet_id": self.wallet_id, "is_deleted": 0}),
        )

    def test_generate_due_rejects_invalid_mode(self):
        response = generate_due(wallet_id=self.wallet_id, mode="replace_all")
        self.assertEqual(getattr(response, "status_code", None), 422)
