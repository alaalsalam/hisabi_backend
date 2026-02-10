import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.recurring import generate, upsert_rule
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestRecurringGenerateAPI(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"recurring_gen_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Recurring",
                "last_name": "Generate",
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
                "client_id": f"acc-rec-gen-{self.suffix}",
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
                "client_id": f"cat-rec-gen-{self.suffix}",
                "category_name": "Transport",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_generate_dry_run_write_and_idempotency(self):
        rule_id = f"rrule-gen-{self.suffix}"
        result = upsert_rule(
            wallet_id=self.wallet_id,
            client_id=rule_id,
            title="Monthly Transport",
            transaction_type="expense",
            amount=15,
            currency="SAR",
            category_id=self.category.name,
            account_id=self.account.name,
            start_date="2026-01-31",
            rrule_type="monthly",
            interval=1,
            bymonthday=31,
            end_mode="none",
            is_active=1,
        )
        self.assertEqual(result.get("status"), "ok")

        dry = generate(
            wallet_id=self.wallet_id,
            from_date="2026-02-01",
            to_date="2026-04-30",
            dry_run=1,
            device_id=self.device_id,
        )
        self.assertEqual(dry.get("status"), "ok")
        self.assertTrue(dry.get("dry_run"))
        self.assertGreaterEqual(len(dry.get("warnings") or []), 1)

        before_instances = frappe.db.count("Hisabi Recurring Instance", {"wallet_id": self.wallet_id, "is_deleted": 0})
        self.assertEqual(before_instances, 0)
        before_txs = frappe.db.count("Hisabi Transaction", {"wallet_id": self.wallet_id, "is_deleted": 0})
        self.assertEqual(before_txs, 0)

        write = generate(
            wallet_id=self.wallet_id,
            from_date="2026-02-01",
            to_date="2026-04-30",
            dry_run=0,
            device_id=self.device_id,
        )
        self.assertEqual(write.get("status"), "ok")
        self.assertFalse(write.get("dry_run"))
        self.assertGreaterEqual(write.get("generated"), 1)
        self.assertGreaterEqual(len(write.get("created_instance_ids") or []), 1)

        after_instances = frappe.db.count("Hisabi Recurring Instance", {"wallet_id": self.wallet_id, "is_deleted": 0})
        self.assertEqual(after_instances, write.get("generated") + write.get("skipped"))

        rerun = generate(
            wallet_id=self.wallet_id,
            from_date="2026-02-01",
            to_date="2026-04-30",
            dry_run=0,
            device_id=self.device_id,
        )
        self.assertEqual(rerun.get("status"), "ok")
        self.assertEqual(rerun.get("generated"), 0)
        self.assertGreaterEqual(rerun.get("skipped"), 1)

    def test_generate_invalid_input_returns_422(self):
        response = generate(wallet_id=self.wallet_id, from_date="2026-04-10", to_date="2026-01-10", dry_run=1)
        self.assertEqual(getattr(response, "status_code", None), 422)
