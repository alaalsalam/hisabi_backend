"""Coverage for the near-real-time wallet change-signal (P1).

The client polls `sync_event_status` every ~5s (foregrounded) and triggers a sync when
`changed` flips true — this is what makes a shared wallet feel live (~5s propagation) without a
persistent socket. This test locks in that contract: a push raises the signal; an up-to-date
cursor sees no change.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.sync import sync_event_status as _sync_event_status
from hisabi_backend.install import ensure_roles
from hisabi_backend.hisabi_backend.tests.test_sync import sync_push


def _msg(response):
    if isinstance(response, dict):
        return response.get("message", response)
    if hasattr(response, "get_data"):
        payload = json.loads(response.get_data(as_text=True) or "{}")
        return payload.get("message", payload)
    return response


class TestRealtimeSignal(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"rt_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "RT", "last_name": "Signal",
            "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.device_id = f"dev-{frappe.generate_hash(length=6)}"
        dev = register_device(self.device_id, "android", "Pixel")
        self.token = dev.get("device_token")
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.token}"}}
        )()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="RT Wallet", device_id=self.device_id)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _signal(self, since_ms=0):
        return _msg(_sync_event_status(device_id=self.device_id, wallet_id=self.wallet_id, since_ms=since_ms))

    def test_push_raises_change_signal_and_cursor_clears_it(self):
        # A push updates the wallet change signal → a client polling from cursor 0 sees `changed`.
        sync_push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-rt-acc", "entity_type": "Hisabi Account",
                "entity_id": "acc-rt", "operation": "create",
                "payload": {
                    "client_id": "acc-rt", "wallet_id": self.wallet_id,
                    "account_name": "Cash", "account_type": "cash",
                    "currency": "SAR", "opening_balance": 0,
                },
            }],
        )
        after = self._signal(since_ms=0)
        self.assertTrue(after.get("changed"), "a push must raise the wallet change signal")
        latest = int(after.get("latest_seq_ms") or 0)
        self.assertGreater(latest, 0, "latest_seq_ms must advance after a push")

        # A client already caught up to `latest` must see no further change (no wasted sync).
        caught_up = self._signal(since_ms=latest)
        self.assertFalse(caught_up.get("changed"), "an up-to-date cursor must report no change")

    def test_signal_requires_wallet_membership(self):
        # A device must not read the change signal of a wallet it does not belong to: the response
        # must be an error envelope, never a normal `changed` payload for a foreign wallet.
        foreign_wallet = f"wallet-{frappe.generate_hash(length=6)}"  # valid format, not our member wallet
        resp = _msg(_sync_event_status(device_id=self.device_id, wallet_id=foreign_wallet, since_ms=0))
        # A normal signal is a dict carrying `changed`; a denied/unknown wallet must NOT be that.
        leaked_signal = isinstance(resp, dict) and "changed" in resp
        self.assertFalse(leaked_signal, "must not leak a foreign wallet change signal")
