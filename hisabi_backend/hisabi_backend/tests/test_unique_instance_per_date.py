import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestUniqueInstancePerDate(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"unique_inst_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Unique",
                "last_name": "Instance",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.suffix = frappe.generate_hash(length=6)

        self.wallet_id = ensure_default_wallet_for_user(self.user.name, device_id=f"device-unique-{self.suffix}")

        self.account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-unique-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "wallet_id": self.wallet_id,
                "user": self.user.name,
            }
        ).insert(ignore_permissions=True)

        self.rule = frappe.get_doc(
            {
                "doctype": "Hisabi Recurring Rule",
                "client_id": f"rrule-unique-{self.suffix}",
                "title": "Daily Rule",
                "transaction_type": "income",
                "amount": 10,
                "currency": "SAR",
                "account_id": self.account.name,
                "start_date": "2026-01-01",
                "rrule_type": "daily",
                "interval": 1,
                "end_mode": "none",
                "wallet_id": self.wallet_id,
                "user": self.user.name,
            }
        ).insert(ignore_permissions=True)

    def test_unique_instance_guard(self):
        base = {
            "doctype": "Hisabi Recurring Instance",
            "wallet_id": self.wallet_id,
            "rule_id": self.rule.name,
            "occurrence_date": "2026-02-10",
            "status": "generated",
            "user": self.user.name,
        }

        first = frappe.get_doc({**base, "client_id": f"rinst-1-{self.suffix}"}).insert(ignore_permissions=True)
        self.assertTrue(first.name)

        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({**base, "client_id": f"rinst-2-{self.suffix}"}).insert(ignore_permissions=True)
