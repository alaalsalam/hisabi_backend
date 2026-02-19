import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.user_lifecycle import delete_user_account_and_data, set_user_frozen_state
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestUserLifecycle(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _create_user(self, prefix: str) -> str:
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": f"{prefix}_{frappe.generate_hash(length=6)}@example.com",
                "first_name": "Hisabi",
                "last_name": "User",
                "send_welcome_email": 0,
                "enabled": 1,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test1234")
        return user.name

    def test_freeze_blocks_device_token_auth(self):
        user = self._create_user("freeze")
        wallet_id = ensure_default_wallet_for_user(user)
        self.assertTrue(wallet_id)

        from hisabi_backend.utils.security import issue_device_token_for_device

        token, device = issue_device_token_for_device(
            user=user,
            device_id=f"device-{frappe.generate_hash(length=8)}",
            platform="web",
            wallet_id=wallet_id,
        )
        self.assertTrue(token)

        previous_request = getattr(frappe.local, "request", None)
        try:
            frappe.local.request = type(
                "obj",
                (object,),
                {
                    "headers": {"Authorization": f"Bearer {token}"},
                    "remote_addr": "127.0.0.1",
                },
            )()
            authed_user, _ = require_device_token_auth(expected_device_id=device.device_id)
            self.assertEqual(authed_user, user)

            set_user_frozen_state(user, freeze=True, actor="Administrator", reason="QA freeze")
            with self.assertRaises(frappe.AuthenticationError):
                require_device_token_auth(expected_device_id=device.device_id)
        finally:
            frappe.local.request = previous_request

    def test_delete_user_account_and_related_data(self):
        user = self._create_user("delete")
        wallet_id = ensure_default_wallet_for_user(user)
        self.assertTrue(wallet_id)

        account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-{frappe.generate_hash(length=10)}",
                "wallet_id": wallet_id,
                "user": user,
                "account_name": "Temp Account",
                "account_type": "cash",
                "currency": "USD",
            }
        ).insert(ignore_permissions=True)
        self.assertTrue(account.name)

        result = delete_user_account_and_data(user, actor="Administrator", delete_frappe_user=True)
        self.assertEqual(result.get("user"), user)
        self.assertFalse(frappe.db.exists("User", user))
        self.assertFalse(frappe.db.exists("Hisabi Wallet", wallet_id))
        self.assertFalse(frappe.db.exists("Hisabi Account", account.name))

