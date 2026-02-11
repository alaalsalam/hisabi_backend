import frappe
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1 import login as v1_login
from hisabi_backend.api.v1 import me as v1_me
from hisabi_backend.api.v1.auth_v2 import register_user as register_user_v2
from hisabi_backend.install import ensure_roles


class TestAuthExpectHeader(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _register(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"
        response = register_user_v2(
            phone=phone,
            full_name="Expect Header User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        return {
            "phone": phone,
            "password": password,
            "device_id": device_id,
            "token": response.get("auth", {}).get("token"),
            "user": response.get("user", {}).get("name"),
        }

    def test_login_tolerates_expect_header_without_417(self):
        setup = self._register()
        frappe.local.request = frappe._dict(headers={"Expect": "100-continue"}, remote_addr="1.2.3.4")
        frappe.local.response = {}

        result = v1_login(
            identifier=setup["phone"],
            password="wrong-password",
            device={"device_id": setup["device_id"], "platform": "android"},
        )

        status = frappe.local.response.get("http_status_code")
        self.assertNotEqual(status, 417)
        self.assertIn(status, {400, 401})
        self.assertIn((result.get("error") or {}).get("code"), {"VALIDATION_ERROR", "INVALID_CREDENTIALS"})

    def test_me_tolerates_expect_header_without_417(self):
        setup = self._register()
        frappe.local.request = frappe._dict(
            headers={"Authorization": f"Bearer {setup['token']}", "Expect": "100-continue"},
            remote_addr="1.2.3.4",
        )
        frappe.local.response = {}

        result = v1_me()

        status = frappe.local.response.get("http_status_code", 200)
        self.assertNotEqual(status, 417)
        self.assertEqual(result.get("user", {}).get("name"), setup["user"])

    def test_me_invalid_token_with_expect_is_401_not_417(self):
        frappe.local.request = frappe._dict(
            headers={"Authorization": "Bearer invalid-token", "Expect": "100-continue"},
            remote_addr="1.2.3.4",
        )
        frappe.local.response = {}

        result = v1_me()

        self.assertEqual(frappe.local.response.get("http_status_code"), 401)
        self.assertNotEqual(frappe.local.response.get("http_status_code"), 417)
        self.assertEqual((result.get("error") or {}).get("code"), "UNAUTHORIZED")
