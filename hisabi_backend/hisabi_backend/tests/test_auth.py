import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import link_device_to_user, login, register, register_device
from hisabi_backend.install import ensure_roles


class TestAuthV1(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def test_register_and_login_with_phone(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"

        response = register(phone=phone, password=password, full_name="Phone User")
        self.assertTrue(response.get("user"))
        self.assertEqual(response.get("profile", {}).get("mobile_no"), phone)

        login_response = login(login=phone, password=password)
        self.assertEqual(login_response.get("user"), response.get("user"))

    def test_link_device_rejects_other_user(self):
        user_a = frappe.get_doc({
            "doctype": "User",
            "email": f"a_{frappe.generate_hash(length=6)}@example.com",
            "first_name": "User",
            "last_name": "A",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user_a.name, "test123")

        frappe.set_user(user_a.name)
        register_device("device-shared", "android", "Pixel")

        user_b = frappe.get_doc({
            "doctype": "User",
            "email": f"b_{frappe.generate_hash(length=6)}@example.com",
            "first_name": "User",
            "last_name": "B",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user_b.name, "test123")

        frappe.set_user(user_b.name)
        with self.assertRaises(frappe.PermissionError):
            link_device_to_user("device-shared")
