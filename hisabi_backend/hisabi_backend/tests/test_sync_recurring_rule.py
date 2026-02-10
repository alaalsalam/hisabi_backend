import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.sync import sync_pull, sync_push
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestSyncRecurringRule(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"sync_rrule_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Sync",
                "last_name": "RecurringRule",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123")
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
                "client_id": f"acc-rrule-{self.suffix}",
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
                "client_id": f"cat-rrule-{self.suffix}",
                "category_name": "Food",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _push(self, items):
        response = sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(response, "status_code", 200), 200, msg=payload)
            response = payload.get("message", payload)
        return response

    def test_sync_recurring_rule_roundtrip(self):
        rule_id = f"rrule-sync-{self.suffix}"
        response = self._push(
            [
                {
                    "op_id": f"op-rrule-{self.suffix}",
                    "entity_type": "Hisabi Recurring Rule",
                    "entity_id": rule_id,
                    "operation": "create",
                    "payload": {
                        "client_id": rule_id,
                        "wallet_id": self.wallet_id,
                        "title": "Weekly Food",
                        "transaction_type": "expense",
                        "amount": 20,
                        "currency": "SAR",
                        "category_id": self.category.name,
                        "account_id": self.account.name,
                        "start_date": "2026-02-01",
                        "rrule_type": "weekly",
                        "interval": 1,
                        "byweekday": '["MO","WE"]',
                        "end_mode": "none",
                        "is_active": 1,
                    },
                }
            ]
        )

        result = response["results"][0]
        self.assertEqual(result.get("status"), "accepted")

        rule = frappe.get_doc("Hisabi Recurring Rule", rule_id)
        self.assertEqual(rule.name, rule.client_id)
        self.assertEqual(rule.wallet_id, self.wallet_id)
        self.assertEqual(rule.title, "Weekly Food")

        pull = sync_pull(device_id=self.device_id, wallet_id=self.wallet_id, since="1970-01-01T00:00:00", limit=500)
        if hasattr(pull, "get_data"):
            payload = json.loads(pull.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(pull, "status_code", 200), 200, msg=payload)
            pull = payload.get("message", payload)

        items = pull.get("items") or []
        recurring_items = [item for item in items if item.get("entity_type") == "Hisabi Recurring Rule"]
        self.assertTrue(any(item.get("entity_id") == rule_id for item in recurring_items))
