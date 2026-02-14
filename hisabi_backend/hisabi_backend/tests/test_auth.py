import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import list_wallets, wallet_create
from hisabi_backend.api.v1.auth import link_device_to_user, login, register, register_device
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import require_device_token_auth


class TestAuthV1(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _create_hisabi_user(self, prefix: str) -> str:
        user = frappe.get_doc({
            "doctype": "User",
            "email": f"{prefix}_{frappe.generate_hash(length=6)}@example.com",
            "first_name": "Hisabi",
            "last_name": "User",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        return user.name

    def test_register_and_login_with_phone(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"

        response = register(phone=phone, password=password, full_name="Phone User")
        self.assertTrue(response.get("user"))
        self.assertEqual(response.get("profile", {}).get("mobile_no"), phone)

        login_response = login(login=phone, password=password)
        self.assertEqual(login_response.get("user"), response.get("user"))

    def test_link_device_rejects_other_user(self):
        user_a = self._create_hisabi_user("a")

        frappe.set_user(user_a)
        register_device("device-shared", "android", "Pixel")

        user_b = self._create_hisabi_user("b")

        frappe.set_user(user_b)
        with self.assertRaises(frappe.PermissionError):
            link_device_to_user("device-shared")

    def test_register_device_sets_wallet_id(self):
        user = self._create_hisabi_user("register_device")
        frappe.set_user(user)
        device_id = f"device-{frappe.generate_hash(length=6)}"
        response = register_device(device_id, "android", "Pixel")
        self.assertTrue(response.get("wallet_id"))
        device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
        self.assertTrue(device_name)
        self.assertEqual(frappe.db.get_value("Hisabi Device", device_name, "wallet_id"), response.get("wallet_id"))

    def test_link_device_to_user_sets_wallet_id(self):
        user = self._create_hisabi_user("link_device")
        frappe.set_user(user)
        device_id = f"device-link-{frappe.generate_hash(length=6)}"
        response = link_device_to_user(device_id, "android")
        self.assertTrue(response.get("wallet_id"))
        device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
        self.assertTrue(device_name)
        self.assertEqual(frappe.db.get_value("Hisabi Device", device_name, "wallet_id"), response.get("wallet_id"))

    def test_list_wallets_returns_existing_wallets(self):
        user = self._create_hisabi_user("list_wallets")
        frappe.set_user(user)
        device_id = f"device-wallets-{frappe.generate_hash(length=6)}"
        device = register_device(device_id, "android", "Pixel")
        token = device.get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {token}"}})()
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Secondary Wallet", device_id=device_id)
        payload = list_wallets(device_id=device_id)
        wallet_ids = payload.get("wallet_ids") or []
        self.assertTrue(payload.get("default_wallet_id"))
        self.assertIn(wallet_id, wallet_ids)
        self.assertIn(payload.get("default_wallet_id"), wallet_ids)

    def test_require_device_token_auth_truncates_user_agent(self):
        user = self._create_hisabi_user("ua_guard")
        frappe.set_user(user)
        device_id = f"device-ua-{frappe.generate_hash(length=6)}"
        device_payload = register_device(device_id, "android", "Pixel")
        token = device_payload.get("device_token")
        self.assertTrue(token)

        previous_request = getattr(frappe.local, "request", None)
        long_user_agent = "Mozilla/5.0 " + ("X" * 300)
        try:
            frappe.local.request = type(
                "obj",
                (object,),
                {
                    "headers": {
                        "Authorization": f"Bearer {token}",
                        "User-Agent": long_user_agent,
                    },
                    "remote_addr": "127.0.0.1",
                },
            )()
            authed_user, _ = require_device_token_auth(expected_device_id=device_id)
            self.assertEqual(authed_user, user)
        finally:
            frappe.local.request = previous_request

        device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
        self.assertTrue(device_name)
        saved_user_agent = frappe.db.get_value("Hisabi Device", device_name, "last_seen_user_agent")
        self.assertIsInstance(saved_user_agent, str)
        max_length = cint(getattr(frappe.get_meta("Hisabi Device").get_field("last_seen_user_agent"), "length", 0) or 140)
        self.assertLessEqual(len(saved_user_agent), max_length)
        self.assertEqual(saved_user_agent, long_user_agent[:max_length])
