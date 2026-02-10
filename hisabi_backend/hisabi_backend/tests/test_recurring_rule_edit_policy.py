import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.recurring import apply_changes, generate, upsert_rule
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestRecurringRuleEditPolicy(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"recurring_edit_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Recurring",
                "last_name": "EditPolicy",
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
                "client_id": f"acc-rec-edit-{self.suffix}",
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
                "client_id": f"cat-rec-edit-{self.suffix}",
                "category_name": "Transport",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_rebuild_scheduled_and_payload_change_applies_to_next_generation(self):
        today = datetime.date.today()
        rule_id = f"rrule-edit-{self.suffix}"
        created = upsert_rule(
            wallet_id=self.wallet_id,
            client_id=rule_id,
            title="Daily Transport",
            transaction_type="expense",
            amount=10,
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
            horizon_days=5,
        )
        self.assertEqual(rebuilt.get("status"), "ok")
        scheduled_created = int((rebuilt.get("counts") or {}).get("created") or 0)
        self.assertGreater(scheduled_created, 0)

        scheduled_instances = frappe.get_all(
            "Hisabi Recurring Instance",
            filters={"wallet_id": self.wallet_id, "rule_id": rule_id, "status": "scheduled", "is_deleted": 0},
            fields=["name", "transaction_id"],
        )
        self.assertTrue(scheduled_instances)
        self.assertTrue(all(not row.transaction_id for row in scheduled_instances))
        self.assertEqual(frappe.db.count("Hisabi Transaction", {"wallet_id": self.wallet_id, "is_deleted": 0}), 0)

        updated = upsert_rule(
            wallet_id=self.wallet_id,
            client_id=rule_id,
            amount=25,
            note="updated payload",
        )
        self.assertEqual(updated.get("status"), "ok")

        generated = generate(
            wallet_id=self.wallet_id,
            from_date=today.isoformat(),
            to_date=(today + datetime.timedelta(days=5)).isoformat(),
            dry_run=0,
            device_id=self.device_id,
        )
        self.assertEqual(generated.get("status"), "ok")
        self.assertGreater(int(generated.get("generated") or 0), 0)

        tx_rows = frappe.get_all(
            "Hisabi Transaction",
            filters={"wallet_id": self.wallet_id, "is_deleted": 0},
            fields=["name", "amount"],
        )
        self.assertTrue(tx_rows)
        self.assertTrue(all(float(tx.amount) == 25.0 for tx in tx_rows))

        rerun = generate(
            wallet_id=self.wallet_id,
            from_date=today.isoformat(),
            to_date=(today + datetime.timedelta(days=5)).isoformat(),
            dry_run=0,
            device_id=self.device_id,
        )
        self.assertEqual(rerun.get("status"), "ok")
        self.assertEqual(int(rerun.get("generated") or 0), 0)
