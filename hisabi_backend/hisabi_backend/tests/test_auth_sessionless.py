import frappe
from frappe.auth import CookieManager
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1 import login as v1_login
from hisabi_backend.api.v1 import register_user as v1_register
from hisabi_backend.install import ensure_roles


class TestAuthSessionless(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _init_request(self):
        frappe.local.request = frappe._dict(headers={}, remote_addr="1.2.3.4")
        frappe.local.response = {}
        frappe.local.cookie_manager = CookieManager()

    def test_register_without_cookies_or_csrf(self):
        self._init_request()
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        result = v1_register(
            phone=phone,
            full_name="Sessionless Register",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        status = frappe.local.response.get("http_status_code")
        self.assertIn(status, {None, 200})
        self.assertIsNotNone(result.get("auth", {}).get("token"))
        self.assertIsNotNone(result.get("user", {}).get("name"))
        self.assertIsNotNone(result.get("default_wallet_id"))
        self.assertEqual(frappe.local.cookie_manager.cookies, {})

    def test_login_without_cookies_or_csrf(self):
        self._init_request()
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"
        v1_register(
            phone=phone,
            full_name="Sessionless Login",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        self._init_request()
        result = v1_login(
            identifier=phone,
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        status = frappe.local.response.get("http_status_code")
        self.assertIn(status, {None, 200})
        self.assertIsNotNone(result.get("auth", {}).get("token"))
        self.assertIsNotNone(result.get("user", {}).get("name"))
        self.assertIsNotNone(result.get("default_wallet_id"))
        self.assertEqual(frappe.local.cookie_manager.cookies, {})
