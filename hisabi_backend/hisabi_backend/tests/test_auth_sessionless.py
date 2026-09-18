import random

import frappe
from frappe.auth import CookieManager
from frappe.tests import IntegrationTestCase as FrappeTestCase

from hisabi_backend.api.v1 import login as v1_login
from hisabi_backend.api.v1 import register_user as v1_register
from hisabi_backend.install import ensure_roles


class TestAuthSessionless(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _init_request(self):
        # A non-`/api/method/login` path keeps LoginManager() out of its auto-login branch,
        # and cookies={} lets Session read an (absent) sid without erroring — the endpoint
        # still refuses to emit cookies, which is what this test asserts.
        frappe.local.request = frappe._dict(
            path="/api/method/hisabi_backend.api.v1.login",
            headers={},
            cookies={},
            scheme="http",
            remote_addr="1.2.3.4",
        )
        frappe.local.request_ip = "1.2.3.4"
        frappe.local.response = {}
        frappe.local.cookie_manager = CookieManager()

    def test_register_without_cookies_or_csrf(self):
        self._init_request()
        phone = "+1555" + "".join(random.choices("0123456789", k=6))
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

    def _assert_no_authenticated_session_cookie(self):
        # login() authenticates via LoginManager (to verify the password), which seeds default
        # Guest cookies; the sessionless guarantee is that NO authenticated session sid is emitted.
        sid_cookie = frappe.local.cookie_manager.cookies.get("sid")
        if sid_cookie is not None:
            self.assertEqual(sid_cookie.get("value"), "Guest")

    def test_login_without_cookies_or_csrf(self):
        self._init_request()
        phone = "+1555" + "".join(random.choices("0123456789", k=6))
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
        self._assert_no_authenticated_session_cookie()
