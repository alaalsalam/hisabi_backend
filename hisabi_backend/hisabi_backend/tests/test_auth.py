import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import link_device_to_user, login, register, register_device
from hisabi_backend.install import ensure_roles


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
