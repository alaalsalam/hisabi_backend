import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.recurring import apply_changes, due, generate, pause_until, skip_instance, upsert_rule
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestRecurringDueAPI(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"recurring_due_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Recurring",
                "last_name": "Due",
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
                "client_id": f"acc-rec-due-{self.suffix}",
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
                "client_id": f"cat-rec-due-{self.suffix}",
                "category_name": "Transport",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _normalize_due_payload(self, payload):
        meta = dict(payload.get("meta") or {})
        meta.pop("generated_at", None)
        meta.pop("server_time", None)
        return {
            "meta": meta,
            "rules": payload.get("rules") or [],
            "due_instances": payload.get("due_instances") or [],
            "stats": payload.get("stats") or {},
        }

    def test_due_is_deterministic_and_reports_statuses(self):
        today = datetime.date.today()
        end_day = today + datetime.timedelta(days=3)

        generated_rule_id = f"rrule-due-generated-{self.suffix}"
        upsert_rule(
            wallet_id=self.wallet_id,
            client_id=generated_rule_id,
            title="Generated Daily",
            transaction_type="expense",
            amount=20,
            currency="SAR",
            category_id=self.category.name,
            account_id=self.account.name,
            start_date=today.isoformat(),
            rrule_type="daily",
            interval=1,
            end_mode="none",
            is_active=1,
        )
        apply_changes(
            rule_id=generated_rule_id,
            wallet_id=self.wallet_id,
            mode="rebuild_scheduled",
            from_date=today.isoformat(),
            horizon_days=3,
        )
        scheduled = frappe.get_all(
            "Hisabi Recurring Instance",
            filters={"wallet_id": self.wallet_id, "rule_id": generated_rule_id, "status": "scheduled", "is_deleted": 0},
            fields=["name", "occurrence_date"],
            order_by="occurrence_date asc",
        )
        self.assertTrue(scheduled)
        skip_instance(instance_id=scheduled[0].name, wallet_id=self.wallet_id, reason="vacation")
        generate(
            wallet_id=self.wallet_id,
            from_date=today.isoformat(),
            to_date=end_day.isoformat(),
            dry_run=0,
            device_id=self.device_id,
        )

        due_rule_id = f"rrule-due-pending-{self.suffix}"
        upsert_rule(
            wallet_id=self.wallet_id,
            client_id=due_rule_id,
            title="Pending Daily",
            transaction_type="expense",
            amount=13,
            currency="SAR",
            category_id=self.category.name,
            account_id=self.account.name,
            start_date=today.isoformat(),
            rrule_type="daily",
            interval=1,
            end_mode="none",
            is_active=1,
        )

        paused_rule_id = f"rrule-due-paused-{self.suffix}"
        upsert_rule(
            wallet_id=self.wallet_id,
            client_id=paused_rule_id,
            title="Paused Daily",
            transaction_type="expense",
            amount=7,
            currency="SAR",
            category_id=self.category.name,
            account_id=self.account.name,
            start_date=today.isoformat(),
            rrule_type="daily",
            interval=1,
            end_mode="none",
            is_active=1,
        )
        pause_until(
            rule_id=paused_rule_id,
            wallet_id=self.wallet_id,
            until_date=(today + datetime.timedelta(days=2)).isoformat(),
        )

        first = due(wallet_id=self.wallet_id, from_date=today.isoformat(), to_date=end_day.isoformat())
        self.assertIsInstance(first, dict)
        self.assertEqual((first.get("meta") or {}).get("wallet_id"), self.wallet_id)
        self.assertEqual((first.get("meta") or {}).get("from_date"), today.isoformat())
        self.assertEqual((first.get("meta") or {}).get("to_date"), end_day.isoformat())
        self.assertTrue((first.get("meta") or {}).get("version"))
        self.assertTrue((first.get("meta") or {}).get("commit"))

        second = due(wallet_id=self.wallet_id, from_date=today.isoformat(), to_date=end_day.isoformat())
        self.assertEqual(self._normalize_due_payload(first), self._normalize_due_payload(second))

        due_rows = first.get("due_instances") or []
        self.assertTrue(any(row.get("rule_id") == due_rule_id and row.get("status") == "due" for row in due_rows))
        self.assertTrue(any(row.get("rule_id") == generated_rule_id and row.get("status") == "already-generated" for row in due_rows))
        self.assertTrue(
            any(
                row.get("rule_id") == generated_rule_id
                and row.get("status") == "skipped"
                and row.get("skip_reason") == "vacation"
                for row in due_rows
            )
        )
        self.assertTrue(any(row.get("rule_id") == paused_rule_id and row.get("status") == "paused" for row in due_rows))

        stats = first.get("stats") or {}
        due_count = sum(1 for row in due_rows if row.get("status") == "due")
        skipped_count = sum(1 for row in due_rows if row.get("status") == "skipped")
        paused_count = sum(1 for row in due_rows if row.get("status") == "paused")
        self.assertEqual(stats.get("due_count"), due_count)
        self.assertEqual(stats.get("skipped_count"), skipped_count)
        self.assertEqual(stats.get("paused_count"), paused_count)

    def test_due_defaults_date_window(self):
        today = datetime.date.today()
        result = due(wallet_id=self.wallet_id)
        meta = result.get("meta") or {}
        self.assertEqual(meta.get("from_date"), today.isoformat())
        self.assertEqual(meta.get("to_date"), (today + datetime.timedelta(days=7)).isoformat())
