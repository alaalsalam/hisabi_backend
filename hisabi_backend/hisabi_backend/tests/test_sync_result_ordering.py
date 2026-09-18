"""Results must return in the client's ORIGINAL request order even when the server reorders
dependent items for processing (P-audit finding #2). The client pairs responses positionally, so
a permuted response array would make it apply/delete the wrong queue rows.
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.install import ensure_roles
from hisabi_backend.hisabi_backend.tests.test_sync import sync_push


class TestSyncResultOrdering(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"ord_{frappe.generate_hash(6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "Ord", "last_name": "T",
            "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.device_id = f"dev-{frappe.generate_hash(6)}"
        self.token = register_device(self.device_id, "android", "P").get("device_token")
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.token}"}}
        )()
        self.wallet_id = f"wallet-{frappe.generate_hash(6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="W", device_id=self.device_id)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_results_follow_client_order_when_server_reorders_categories(self):
        parent = f"cat-parent-{frappe.generate_hash(4)}"
        child = f"cat-child-{frappe.generate_hash(4)}"
        # Client sends CHILD first, PARENT second. The server must PROCESS parent first (dependency)
        # but RETURN results in the client's original order: [child, parent].
        resp = sync_push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-child", "entity_type": "Hisabi Category", "entity_id": child,
                    "operation": "create",
                    "payload": {
                        "client_id": child, "wallet_id": self.wallet_id, "category_name": "Child",
                        "kind": "expense", "parent_category": parent,
                    },
                },
                {
                    "op_id": "op-parent", "entity_type": "Hisabi Category", "entity_id": parent,
                    "operation": "create",
                    "payload": {
                        "client_id": parent, "wallet_id": self.wallet_id, "category_name": "Parent",
                        "kind": "expense",
                    },
                },
            ],
        )
        results = resp["results"]
        self.assertEqual(len(results), 2)
        # Position 0 must be the CHILD's result, position 1 the PARENT's — matching client order.
        self.assertEqual(results[0].get("client_id"), child, "result[0] must be the child (client order)")
        self.assertEqual(results[1].get("client_id"), parent, "result[1] must be the parent (client order)")
        # And both must have been accepted (parent processed first despite being sent second).
        self.assertEqual(results[0]["status"], "accepted")
        self.assertEqual(results[1]["status"], "accepted")
