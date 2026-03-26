import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint
from frappe.utils.password import update_password

from hisabi_backend.api.v1.bucket_templates import (
    create_bucket_template,
    ensure_wallet_bucket_defaults,
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
        frappe.db.set_value(
            "Hisabi Bucket Template",
            {"wallet_id": self.wallet_id, "is_deleted": 0, "is_default": 1},
            {"is_default": 0, "is_active": 0},
            update_modified=False,
        )

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
        salary_template = next((row for row in templates if row.get("title") == "Salary Split"), None)
        self.assertTrue(salary_template, msg=templates)
        self.assertEqual(salary_template.get("is_default"), 1)
        self.assertEqual(len(salary_template.get("template_items") or []), 2)

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

    def test_sync_push_updates_seeded_default_template_by_client_id(self):
        ensure_wallet_bucket_defaults(self.wallet_id, user=self.user.name)
        template_id = f"{self.wallet_id}:bucket-template:default"
        seeded_name = frappe.get_value(
            "Hisabi Bucket Template",
            {"wallet_id": self.wallet_id, "client_id": template_id, "is_deleted": 0},
            "name",
        )
        self.assertTrue(seeded_name)
        doc = frappe.get_doc("Hisabi Bucket Template", seeded_name)

        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-bucket-template-update-default",
                    "entity_type": "Hisabi Bucket Template",
                    "entity_id": template_id,
                    "operation": "update",
                    "base_version": cint(doc.doc_version or 0),
                    "payload": {
                        "client_id": template_id,
                        "title": "التوزيع النشط",
                        "is_active": 1,
                        "is_default": 1,
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
        self.assertEqual(result.get("status"), "accepted", msg=response)

        doc = frappe.get_doc("Hisabi Bucket Template", template_id)
        self.assertEqual(doc.name, template_id)
        self.assertEqual(doc.client_id, template_id)
        self.assertEqual(doc.title, "التوزيع النشط")

    def test_sync_push_line_create_upserts_existing_rule_bucket_pair(self):
        rule_id = f"rule-sync-{self.suffix}"
        original_line_id = f"line-sync-{self.suffix}-old"
        replacement_line_id = f"line-sync-{self.suffix}-new"

        seed_response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-rule-seed",
                    "entity_type": "Hisabi Allocation Rule",
                    "entity_id": rule_id,
                    "operation": "create",
                    "payload": {
                        "client_id": rule_id,
                        "rule_name": "Seed Rule",
                        "scope_type": "global",
                        "is_default": 1,
                        "active": 1,
                    },
                },
                {
                    "op_id": "op-line-seed",
                    "entity_type": "Hisabi Allocation Rule Line",
                    "entity_id": original_line_id,
                    "operation": "create",
                    "payload": {
                        "client_id": original_line_id,
                        "rule": rule_id,
                        "bucket": self.bucket_a.name,
                        "percent": 60,
                        "sort_order": 0,
                    },
                },
            ],
        )
        if hasattr(seed_response, "get_data"):
            payload = json.loads(seed_response.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(seed_response, "status_code", 200), 200, msg=payload)
            seed_response = payload.get("message", payload)
        self.assertTrue(all(row.get("status") == "accepted" for row in seed_response.get("results", [])), seed_response)
        rule_name = frappe.get_value("Hisabi Allocation Rule", {"client_id": rule_id, "wallet_id": self.wallet_id}, "name")
        self.assertTrue(rule_name)

        response = sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-line-replace",
                    "entity_type": "Hisabi Allocation Rule Line",
                    "entity_id": replacement_line_id,
                    "operation": "create",
                    "payload": {
                        "client_id": replacement_line_id,
                        "rule": rule_id,
                        "bucket": self.bucket_a.name,
                        "percent": 75,
                        "sort_order": 0,
                    },
                }
            ],
        )
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            self.assertEqual(getattr(response, "status_code", 200), 200, msg=payload)
            response = payload.get("message", payload)

        result = (response.get("results") or [{}])[0]
        self.assertEqual(result.get("status"), "accepted", msg=response)

        rows = frappe.get_all(
            "Hisabi Allocation Rule Line",
            filters={"wallet_id": self.wallet_id, "rule": rule_name, "bucket": self.bucket_a.name, "is_deleted": 0},
            fields=["name", "client_id", "percent"],
        )
        self.assertEqual(len(rows), 1, msg=rows)
        self.assertEqual(rows[0].get("client_id"), replacement_line_id)
        self.assertEqual(rows[0].get("percent"), 75)

    def test_ensure_wallet_bucket_defaults_repairs_category_links(self):
        category = frappe.get_doc(
            {
                "doctype": "Hisabi Category",
                "client_id": "cat-groceries",
                "category_name": "بقالة البيت",
                "kind": "expense",
                "user": self.user.name,
                "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        outcome = ensure_wallet_bucket_defaults(self.wallet_id, user=self.user.name)
        self.assertGreaterEqual(outcome.get("created_buckets", 0), 0)

        category.reload()
        self.assertTrue(category.default_bucket)

        bucket = frappe.get_doc("Hisabi Bucket", category.default_bucket)
        self.assertIn(bucket.title, {"الشخصية", "الالتزامات", "الادخار", "الاستثمار", "الصحة", "الصدقات"})

        default_payload = get_default_bucket_template(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertTrue((default_payload.get("template") or {}).get("id"))
