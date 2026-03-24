import frappe
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


class TestAttachmentsSync(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _set_bearer(self, token: str):
        frappe.local.request = frappe._dict(headers={"Authorization": f"Bearer {token}"}, remote_addr="1.2.3.4")

    def _sync_push_message(self, **kwargs):
        response = sync_push(**kwargs)
        if isinstance(response, dict):
            return response
        if hasattr(response, "get_data"):
            payload = frappe.parse_json(response.get_data(as_text=True) or "{}")
            return payload.get("message", payload)
        return response

    def test_attachment_sync_create(self):
        device_id = f"dev-{frappe.generate_hash(length=8)}"
        device = register_device(device_id, "android", "Pixel Attachment")
        token = device["device_token"]
        self._set_bearer(token)
        wallet_id = f"wallet-att-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Attachment Wallet", device_id=device_id)

        now = frappe.utils.now_datetime().isoformat()
        items = [
            {
                "op_id": "op-acc-test-1",
                "entity_type": "Hisabi Account",
                "entity_id": "acc-test-1",
                "operation": "create",
                "payload": {
                    "client_id": "acc-test-1",
                    "name": "Cash",
                    "type": "cash",
                    "currency": "SAR",
                    "opening_balance": 100,
                },
            },
            {
                "op_id": "op-tx-test-1",
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
                "op_id": "op-att-test-1",
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

        out = self._sync_push_message(device_id=device_id, wallet_id=wallet_id, items=items)
        results = out.get("results") or []
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.get("status") == "accepted" for r in results), out)

        doc = frappe.get_doc("Hisabi Attachment", "att-test-1")
        self.assertEqual(doc.owner_entity_type, "Hisabi Transaction")
        self.assertEqual(doc.owner_client_id, "tx-test-1")
        self.assertEqual(doc.wallet_id, wallet_id)
