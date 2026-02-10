import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.recurring import apply_changes, generate, skip_instance, upsert_rule
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestRecurringInstanceSkip(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"recurring_skip_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Recurring",
                "last_name": "Skip",
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
                "client_id": f"acc-rec-skip-{self.suffix}",
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
                "client_id": f"cat-rec-skip-{self.suffix}",
                "category_name": "Food",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_skip_blocks_regeneration_and_warns_if_tx_exists(self):
        today = datetime.date.today()
        rule_id = f"rrule-skip-{self.suffix}"
        created = upsert_rule(
            wallet_id=self.wallet_id,
            client_id=rule_id,
            title="Skip Policy",
            transaction_type="expense",
            amount=12,
            currency="SAR",
            category_id=self.category.name,
            account_id=self.account.name,
            start_date=today.isoformat(),
            rrule_type="daily",
            interval=1,
            end_mode="none",
            is_active=1,
        )
        self.assertEqual(created.get("status"), "ok")

        rebuilt = apply_changes(
            rule_id=rule_id,
            wallet_id=self.wallet_id,
            mode="rebuild_scheduled",
            from_date=today.isoformat(),
            horizon_days=3,
        )
        self.assertEqual(rebuilt.get("status"), "ok")

        scheduled = frappe.get_all(
            "Hisabi Recurring Instance",
            filters={"wallet_id": self.wallet_id, "rule_id": rule_id, "status": "scheduled", "is_deleted": 0},
            fields=["name", "occurrence_date"],
            order_by="occurrence_date asc",
        )
        self.assertTrue(scheduled)

        skip_resp = skip_instance(instance_id=scheduled[0].name, wallet_id=self.wallet_id, reason="vacation")
        self.assertEqual(skip_resp.get("status"), "ok")
        self.assertEqual((skip_resp.get("instance") or {}).get("status"), "skipped")

        write = generate(
            wallet_id=self.wallet_id,
            from_date=today.isoformat(),
            to_date=(today + datetime.timedelta(days=3)).isoformat(),
            dry_run=0,
            device_id=self.device_id,
        )
        self.assertEqual(write.get("status"), "ok")

        skipped_doc = frappe.get_doc("Hisabi Recurring Instance", scheduled[0].name)
        self.assertEqual(skipped_doc.status, "skipped")
        self.assertFalse(skipped_doc.transaction_id)

        generated_instance = frappe.get_all(
            "Hisabi Recurring Instance",
            filters={"wallet_id": self.wallet_id, "rule_id": rule_id, "status": "generated", "is_deleted": 0},
            fields=["name", "transaction_id"],
            limit=1,
        )[0]
        warning_resp = skip_instance(instance_id=generated_instance.name, wallet_id=self.wallet_id, reason="manual adjust")
        warnings = warning_resp.get("warnings") or []
        self.assertTrue(any(item.get("warning") == "tx_exists" for item in warnings))
        self.assertTrue(
            frappe.db.exists(
                "Hisabi Transaction",
                {"name": generated_instance.transaction_id, "wallet_id": self.wallet_id, "is_deleted": 0},
            )
        )
