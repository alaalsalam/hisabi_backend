import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.recurring import apply_changes, preview, upsert_rule
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestRecurringPreviewAPI(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"recurring_preview_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Recurring",
                "last_name": "Preview",
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
                "client_id": f"acc-rec-preview-{self.suffix}",
                "account_name": "Cash",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_preview_marks_existing_and_reports_invalid_day(self):
        rule_id = f"rrule-preview-{self.suffix}"
        created = upsert_rule(
            wallet_id=self.wallet_id,
            client_id=rule_id,
            title="Monthly Edge",
            transaction_type="income",
            amount=100,
            currency="SAR",
            account_id=self.account.name,
            start_date="2026-01-31",
            rrule_type="monthly",
            interval=1,
            bymonthday=31,
            end_mode="none",
            is_active=1,
        )
        self.assertEqual(created.get("status"), "ok")

        first_preview = preview(
            wallet_id=self.wallet_id,
            rule_id=rule_id,
            from_date="2026-02-01",
            to_date="2026-04-30",
        )
        self.assertEqual(first_preview.get("status"), "ok")
        warnings = first_preview.get("warnings") or []
        self.assertTrue(any(w.get("reason") == "invalid_day" for w in warnings))
        self.assertTrue(any(row.get("would_create") is True for row in first_preview.get("occurrences") or []))

        rebuild = apply_changes(
            rule_id=rule_id,
            wallet_id=self.wallet_id,
            mode="rebuild_scheduled",
            from_date="2026-02-01",
            horizon_days=90,
        )
        self.assertEqual(rebuild.get("status"), "ok")

        second_preview = preview(
            wallet_id=self.wallet_id,
            rule_id=rule_id,
            from_date="2026-02-01",
            to_date="2026-04-30",
        )
        self.assertEqual(second_preview.get("status"), "ok")
        self.assertTrue(any(row.get("would_create") is False for row in second_preview.get("occurrences") or []))
