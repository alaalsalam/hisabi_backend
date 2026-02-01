import frappe
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1.auth_v2 import register_user as register_user_v2
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


class TestAttachmentsSync(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _set_bearer(self, token: str):
        frappe.local.request = frappe._dict(headers={"Authorization": f"Bearer {token}"}, remote_addr="1.2.3.4")

    def test_attachment_sync_create(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="Attachment User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        token = res["auth"]["token"]
        wallet_id = res["default_wallet_id"]
        self._set_bearer(token)

        now = frappe.utils.now_datetime().isoformat()
        items = [
            {
                "entity_type": "Hisabi Account",
                "entity_id": "acc-test-1",
                "operation": "create",
                "payload": {
                    "client_id": "acc-test-1",
                    "name": "Cash",
                    "type": "cash",
                    "currency": "SAR",
                    "opening_balance": 100,
                    "current_balance": 100,
                },
            },
            {
                "entity_type": "Hisabi Transaction",
                "entity_id": "tx-test-1",
                "operation": "create",
                "payload": {
                    "client_id": "tx-test-1",
                    "type": "expense",
                    "date_time": now,
                    "amount": 10,
                    "currency": "SAR",
                    "account_id": "acc-test-1",
                    "note": "Test",
                },
            },
            {
                "entity_type": "Hisabi Attachment",
                "entity_id": "att-test-1",
                "operation": "create",
                "payload": {
                    "client_id": "att-test-1",
                    "owner_entity_type": "Hisabi Transaction",
                    "owner_client_id": "tx-test-1",
                    "file_id": "file-test-1",
                    "file_url": "https://example.com/file-test-1",
                    "file_name": "receipt.jpg",
                    "mime_type": "image/jpeg",
                    "file_size": 1234,
                },
            },
        ]

        out = sync_push(device_id=device_id, wallet_id=wallet_id, items=items)
        results = out.get("results") or []
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.get("status") == "accepted" for r in results))

        doc = frappe.get_doc("Hisabi Attachment", "att-test-1")
        self.assertEqual(doc.owner_entity_type, "Hisabi Transaction")
        self.assertEqual(doc.owner_client_id, "tx-test-1")
        self.assertEqual(doc.wallet_id, wallet_id)
