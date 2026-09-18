import random

import frappe
from frappe.auth import CookieManager
from frappe.tests import IntegrationTestCase as FrappeTestCase

from hisabi_backend.api.v1.auth_v2 import login as login_v2
from hisabi_backend.api.v1.auth_v2 import register_user as register_user_v2
from hisabi_backend.install import ensure_roles


def _phone():
    return "+1555" + "".join(random.choices("0123456789", k=6))


def _device_id():
    return "dev-" + "".join(random.choices("0123456789", k=8))


class TestSecurityRateLimits(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        self._init_request()

    def _init_request(self, ip="9.9.9.9"):
        # LoginManager() needs cookies to exist; a non-login path avoids its auto-login branch.
        frappe.local.request = frappe._dict(
            path="/api/method/hisabi_backend.api.v1.auth_v2.login",
            headers={},
            cookies={},
            scheme="http",
            remote_addr=ip,
        )
        frappe.local.request_ip = ip
        frappe.local.response = frappe._dict()
        frappe.local.cookie_manager = CookieManager()

    def test_rate_limit_register(self):
        # register applies an IP rate limit (20/600s) BEFORE any work; the 21st call from the
        # same IP raises TooManyRequestsError. Individual calls may also return other errors
        # (e.g. Frappe's global user-creation throttle) — the rate-limit counter still advances.
        # A fresh random IP per run keeps the Redis counter starting at zero across re-runs.
        ip = "10." + ".".join(str(random.randint(1, 254)) for _ in range(3))
        for _ in range(20):
            self._init_request(ip)
            register_user_v2(
                phone=_phone(),
                full_name="User",
                password="testpass123",
                device={"device_id": _device_id(), "platform": "android"},
            )

        self._init_request(ip)
        with self.assertRaises(frappe.TooManyRequestsError):
            register_user_v2(
                phone=_phone(),
                full_name="User",
                password="testpass123",
                device={"device_id": _device_id(), "platform": "android"},
            )

    def test_login_lockout_after_failures(self):
        # login() never raises: wrong credentials return an INVALID_CREDENTIALS error dict,
        # and after 5 failures the account is locked (even a correct password is rejected).
        phone = _phone()
        device_id = _device_id()
        register_user_v2(
            phone=phone,
            full_name="User",
            password="testpass123",
            device={"device_id": device_id, "platform": "android"},
        )

        for _ in range(5):
            self._init_request()
            res = login_v2(identifier=phone, password="wrongpass", device={"device_id": device_id, "platform": "android"})
            self.assertEqual(res.get("error", {}).get("code"), "INVALID_CREDENTIALS")

        self._init_request()
        res = login_v2(identifier=phone, password="testpass123", device={"device_id": device_id, "platform": "android"})
        self.assertIsNone(res.get("auth"))
        self.assertEqual(res.get("error", {}).get("code"), "INVALID_CREDENTIALS")
