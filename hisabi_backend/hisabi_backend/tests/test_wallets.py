import frappe
from frappe.tests.utils import FrappeTestCase
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

        # Not a member yet: pull should fail.
        with self.assertRaises(frappe.PermissionError):
            sync_pull(device_id=device2_id, wallet_id=wallet_id)

        wallet_invite_accept(invite_code=invite["invite_code"], device_id=device2_id)

        # Audit events recorded
        self.assertTrue(frappe.db.exists("Hisabi Audit Log", {"event_type": "wallet_invite_created"}))
        self.assertTrue(frappe.db.exists("Hisabi Audit Log", {"event_type": "wallet_invite_accepted"}))

        pull = sync_pull(device_id=device2_id, wallet_id=wallet_id)
        self.assertIn("Hisabi Wallet", pull.get("changes", {}))
        self.assertIn("Hisabi Wallet Member", pull.get("changes", {}))

        wl = wallets_list(device_id=device2_id)["wallets"]
        self.assertTrue(any((row.get("wallet") == wallet_id) for row in wl))

    def test_viewer_cannot_sync_push(self):
        user1 = self._new_user("owner2")
        device1_id, _ = self._auth_as(user1)
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="ReadOnly", device_id=device1_id)
        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="viewer", device_id=device1_id)["invite"]

        user2 = self._new_user("viewer2")
        device2_id, _ = self._auth_as(user2)
        wallet_invite_accept(invite_code=invite["invite_code"], device_id=device2_id)

        with self.assertRaises(frappe.PermissionError):
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

    def test_removed_member_blocked(self):
        owner = self._new_user("owner3")
        owner_device, _ = self._auth_as(owner)
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Team", device_id=owner_device)
        invite = wallet_invite_create(wallet_id=wallet_id, role_to_grant="member", device_id=owner_device)["invite"]

        member = self._new_user("member3")
        member_device, _ = self._auth_as(member)
        wallet_invite_accept(invite_code=invite["invite_code"], device_id=member_device)

        # Remove member.
        wallet_member_remove(wallet_id=wallet_id, user_to_remove=member.name, device_id=owner_device)

        with self.assertRaises(frappe.PermissionError):
            sync_pull(device_id=member_device, wallet_id=wallet_id)
