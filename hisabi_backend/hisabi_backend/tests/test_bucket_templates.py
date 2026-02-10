import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.bucket_templates import (
    create_bucket_template,
    get_default_bucket_template,
    list_bucket_templates,
)
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBucketTemplates(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"bucket_template_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Bucket",
                "last_name": "Template",
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
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()

        self.bucket_a = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-template-a-{self.suffix}",
                "title": "Ops",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)
        self.bucket_b = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-template-b-{self.suffix}",
                "title": "Savings",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def _template_items(self):
        return [
            {"bucket_id": self.bucket_a.name, "percentage": 60},
            {"bucket_id": self.bucket_b.name, "percentage": 40},
        ]

    def test_api_create_list_and_get_default(self):
        created = create_bucket_template(
            wallet_id=self.wallet_id,
            title="Salary Split",
            is_default=1,
            is_active=1,
            template_items=self._template_items(),
            device_id=self.device_id,
        )
        self.assertEqual((created.get("template") or {}).get("title"), "Salary Split")

        listed = list_bucket_templates(wallet_id=self.wallet_id, include_inactive=1, device_id=self.device_id)
        templates = listed.get("templates") or []
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0].get("is_default"), 1)
        self.assertEqual(len(templates[0].get("template_items") or []), 2)

        default_payload = get_default_bucket_template(wallet_id=self.wallet_id, device_id=self.device_id)
        default_template = default_payload.get("template") or {}
        self.assertEqual(default_template.get("title"), "Salary Split")

    def test_validate_rejects_percent_total_not_100(self):
        doc = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket Template",
                "client_id": f"template-invalid-{self.suffix}",
                "title": "Invalid",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
                "is_active": 1,
                "template_items": [
                    {"bucket_id": self.bucket_a.name, "percentage": 50},
                    {"bucket_id": self.bucket_b.name, "percentage": 30},
                ],
            }
        )
        self.assertRaises(frappe.ValidationError, doc.insert, ignore_permissions=True)

    def test_bucket_archive_blocked_when_used_by_active_template(self):
        create_bucket_template(
            wallet_id=self.wallet_id,
            title="Guardrail Template",
            is_default=1,
            is_active=1,
            template_items=self._template_items(),
            device_id=self.device_id,
        )

        bucket = frappe.get_doc("Hisabi Bucket", self.bucket_a.name)
        bucket.archived = 1
        with self.assertRaises(frappe.ValidationError):
            bucket.save(ignore_permissions=True)

    def test_sync_push_create_bucket_template(self):
        template_id = f"template-sync-{self.suffix}"
        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-bucket-template-sync",
                    "entity_type": "Hisabi Bucket Template",
                    "entity_id": template_id,
                    "operation": "create",
                    "payload": {
                        "client_id": template_id,
                        "title": "Sync Template",
                        "is_default": 1,
                        "is_active": 1,
                        "template_items": self._template_items(),
                    },
                }
            ],
        )

        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(response, "status_code", 200), 200, msg=payload)
            response = payload.get("message", payload)

        result = (response.get("results") or [{}])[0]
        self.assertEqual(result.get("status"), "accepted")

        doc = frappe.get_doc("Hisabi Bucket Template", template_id)
        self.assertEqual(doc.title, "Sync Template")
        self.assertEqual(len(doc.template_items or []), 2)
