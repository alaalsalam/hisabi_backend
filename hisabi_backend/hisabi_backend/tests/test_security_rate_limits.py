import frappe
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1.auth_v2 import login as login_v2
from hisabi_backend.api.v1.auth_v2 import register_user as register_user_v2


class TestSecurityRateLimits(FrappeTestCase):
    def setUp(self):
        frappe.local.request = frappe._dict(headers={}, remote_addr="9.9.9.9")

    def test_rate_limit_register(self):
        # 3 allowed, 4th should 429
        for i in range(3):
            register_user_v2(
                phone=f"+1555{frappe.generate_hash(length=6)}",
                full_name="User",
                password="testpass123",
                device={"device_id": f"dev-{frappe.generate_hash(length=8)}", "platform": "android"},
            )
        with self.assertRaises(frappe.TooManyRequestsError):
            register_user_v2(
                phone=f"+1555{frappe.generate_hash(length=6)}",
                full_name="User",
                password="testpass123",
                device={"device_id": f"dev-{frappe.generate_hash(length=8)}", "platform": "android"},
            )

    def test_rate_limit_login(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
        register_user_v2(
            phone=phone,
            full_name="User",
            password="testpass123",
            device={"device_id": f"dev-{frappe.generate_hash(length=8)}", "platform": "android"},
        )

        for _ in range(5):
            with self.assertRaises(Exception):
                login_v2(identifier=phone, password="wrongpass", device={"device_id": "dev1", "platform": "android"})

        with self.assertRaises(frappe.TooManyRequestsError):
            login_v2(identifier=phone, password="wrongpass", device={"device_id": "dev1", "platform": "android"})

