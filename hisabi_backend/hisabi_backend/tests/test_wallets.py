import json

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import (
    wallet_create,
    wallet_invite_accept,
    wallet_invite_create,
    wallet_member_remove,
    wallets_list,
)
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.sync import sync_pull, sync_push
from hisabi_backend.install import ensure_roles


class TestSharedWallets(FrappeTestCase):
    def _pull_message(self, response):
        if isinstance(response, dict):
            return response
        if hasattr(response, "get_data"):
            payload = json.loads(response.get_data(as_text=True) or "{}")
            return payload.get("message", payload)
        return response

    def _raw_payload(self, response):
        if isinstance(response, dict):
            return response
        if hasattr(response, "get_data"):
            return json.loads(response.get_data(as_text=True) or "{}")
        return response

    def _new_user(self, prefix: str) -> frappe.model.document.Document:
        email = f"{prefix}_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": prefix,
                "last_name": "Tester",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test12345")
        return user

    def _auth_as(self, user: frappe.model.document.Document) -> tuple[str, str]:
        frappe.set_user(user.name)
        device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(device_id, "android", "Pixel 8")
        token = device.get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {token}"}})()
        return device_id, token

    def setUp(self):
        ensure_roles()

    def test_wallet_create_invite_accept_and_sync(self):
        user1 = self._new_user("wallet_owner")
        device1_id, _ = self._auth_as(user1)

        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Family", device_id=device1_id)

        # Invite user2 as member.
        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="member", device_id=device1_id)["invite"]

        user2 = self._new_user("wallet_member")
        device2_id, _ = self._auth_as(user2)

        # Not a member yet: pull is rejected with a structured auth error (does not raise).
        denied = self._raw_payload(sync_pull(device_id=device2_id, wallet_id=wallet_id))
        self.assertEqual(denied.get("error"), "auth_failed")

        wallet_invite_accept(invite_code=invite["invite_code"], device_id=device2_id)

        # Audit events recorded
        self.assertTrue(frappe.db.exists("Hisabi Audit Log", {"event_type": "wallet_invite_created"}))
        self.assertTrue(frappe.db.exists("Hisabi Audit Log", {"event_type": "wallet_invite_accepted"}))

        pull = self._pull_message(sync_pull(device_id=device2_id, wallet_id=wallet_id))
        items = pull.get("items", [])
        self.assertTrue(any(row.get("entity_type") == "Hisabi Wallet" for row in items))
        self.assertTrue(any(row.get("entity_type") == "Hisabi Wallet Member" for row in items))

        wl = wallets_list(device_id=device2_id)["wallets"]
        self.assertTrue(any((row.get("wallet") == wallet_id) for row in wl))

    def test_arabic_client_id_wallet_create_and_invite(self):
        # Users with an Arabic name generate ids like `wallet-محمد-1234-001`; these must be accepted at
        # create AND invite (previously rejected with "Invalid client_id format").
        user = self._new_user("ar_owner")
        device_id, _ = self._auth_as(user)
        wallet_id = f"wallet-محمد-{frappe.generate_hash(length=4)}-1"
        wallet_create(client_id=wallet_id, wallet_name="محفظتي", device_id=device_id)
        self.assertTrue(frappe.db.exists("Hisabi Wallet", wallet_id))

        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="member", device_id=device_id)["invite"]
        self.assertTrue(invite.get("invite_code"))

    def test_owner_not_demoted_by_accepting_own_invite(self):
        # Single-account flow: the owner creates an invite (role_to_grant='member') then accepts their
        # own code. Accepting must never demote the owner, or the wallet would be left with no owner.
        owner = self._new_user("self_owner")
        device_id, _ = self._auth_as(owner)
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Solo", device_id=device_id)
        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="member", device_id=device_id)["invite"]
        wallet_invite_accept(invite_code=invite["invite_code"], device_id=device_id)
        self.assertEqual(
            frappe.db.get_value("Hisabi Wallet Member", {"wallet": wallet_id, "user": owner.name}, "role"),
            "owner",
        )

    def test_viewer_cannot_sync_push(self):
        user1 = self._new_user("owner2")
        device1_id, _ = self._auth_as(user1)
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="ReadOnly", device_id=device1_id)
        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="viewer", device_id=device1_id)["invite"]

        user2 = self._new_user("viewer2")
        device2_id, _ = self._auth_as(user2)
        wallet_invite_accept(invite_code=invite["invite_code"], device_id=device2_id)

        # Viewer is read-only: push does not raise, it returns a structured error and writes nothing.
        push = self._raw_payload(
            sync_push(
                device_id=device2_id,
                wallet_id=wallet_id,
                items=[
                    {
                        "op_id": "op-acc-v1",
                        "entity_type": "Hisabi Account",
                        "entity_id": "acc-v1",
                        "operation": "create",
                        "payload": {
                            "client_id": "acc-v1",
                            "account_name": "Cash",
                            "account_type": "cash",
                            "currency": "SAR",
                            "opening_balance": 0,
                            "client_modified_ms": 1700000000000,
                        },
                    }
                ],
            )
        )
        self.assertEqual(push.get("error"), "wallet_read_only")
        self.assertFalse(frappe.db.exists("Hisabi Account", {"client_id": "acc-v1"}))

    def test_removed_member_blocked(self):
        owner = self._new_user("owner3")
        owner_device, _ = self._auth_as(owner)
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Team", device_id=owner_device)
        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="member", device_id=owner_device)["invite"]

        member = self._new_user("member3")
        member_device, _ = self._auth_as(member)
        wallet_invite_accept(invite_code=invite["invite_code"], device_id=member_device)

        # Remove member. require_device_token_auth reads the Bearer token from the request,
        # so re-auth as the owner (the last _auth_as set the member's token).
        owner_device, _ = self._auth_as(owner)
        wallet_member_remove(wallet_id=wallet_id, user_to_remove=member.name, device_id=owner_device)

        # Removed member's pull is rejected with a structured auth error (does not raise).
        self._auth_as(member)
        denied = self._raw_payload(sync_pull(device_id=member_device, wallet_id=wallet_id))
        self.assertEqual(denied.get("error"), "auth_failed")
