"""A brand-new user's first sync must deliver the default buckets.

The SPA's local seed does not create buckets (its `buckets` table is empty after first boot); they
come from the server: device registration creates the user's default wallet + default buckets, and
the first sync_pull must return them.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.bucket_templates import DEFAULT_BUCKETS
from hisabi_backend.api.v1.sync import sync_pull
from hisabi_backend.install import ensure_roles


class TestNewUserDefaultBuckets(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": f"newbucket_{frappe.generate_hash(length=6)}@example.com",
                "first_name": "NewBucket",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test12345")
        frappe.set_user(user.name)
        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(self.device_id, "web", "Chromium")
        self.wallet_id = device.get("wallet_id")
        frappe.local.request = type(
            "o", (object,), {"headers": {"Authorization": f"Bearer {device.get('device_token')}"}}
        )()

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_first_pull_returns_default_buckets(self):
        self.assertTrue(self.wallet_id, "register_device must assign a default wallet")
        pull = sync_pull(device_id=self.device_id, wallet_id=self.wallet_id, since="1970-01-01T00:00:00", limit=500)
        if hasattr(pull, "get_data"):  # sync_pull returns an HTTP response
            pull = json.loads(pull.get_data(as_text=True) or "{}")
        pull = pull.get("message", pull)
        bucket_ids = {
            row.get("client_id") for row in pull.get("items") or [] if row.get("entity_type") == "Hisabi Bucket"
        }
        expected = {f"{self.wallet_id}:bucket:{b['key']}" for b in DEFAULT_BUCKETS}
        self.assertTrue(expected <= bucket_ids, f"missing default buckets: {sorted(expected - bucket_ids)}")
