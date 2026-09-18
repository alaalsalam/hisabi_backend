import random

import frappe
from frappe.auth import CookieManager
from frappe.tests import IntegrationTestCase as FrappeTestCase

from hisabi_backend.api.v1.auth_v2 import login as login_v2
from hisabi_backend.api.v1.auth_v2 import me as me_v2
from hisabi_backend.api.v1.auth_v2 import register_user as register_user_v2
from hisabi_backend.api.v1.devices import devices_list
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


def _phone():
    return "+1555" + "".join(random.choices("0123456789", k=6))


def _test_ip():
    # Frappe's native LoginAttemptTracker locks by frappe.local.request_ip (in addition to
    # per-user). A shared static IP across every test would let one test's deliberate failed
    # logins (e.g. wrong-password/lockout scenarios) trip the shared IP bucket and lock out
    # unrelated tests running later in the same process. Give each test its own IP.
    return "10." + ".".join(str(random.randint(1, 254)) for _ in range(3))


class TestAuthV2(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        self._install_http_fixture()

    def _install_http_fixture(self):
        # A non-`/api/method/login` path keeps LoginManager() out of its auto-login branch,
        # so the endpoint's explicit authenticate(user, pwd) call is what runs.
        ip = _test_ip()
        frappe.local.request = frappe._dict(
            path="/api/method/hisabi_backend.api.v1.auth_v2.login",
            headers={},
            cookies={},
            scheme="http",
            remote_addr=ip,
        )
        frappe.local.request_ip = ip
        frappe.local.cookie_manager = CookieManager()

    def _set_bearer(self, token: str):
        frappe.local.request = frappe._dict(headers={"Authorization": f"Bearer {token}"}, remote_addr="1.2.3.4")

    def test_register_and_login_by_phone(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="Phone User",
            password=password,
            device={"device_id": device_id, "platform": "android", "device_name": "Pixel"},
        )
        self.assertTrue(res.get("auth", {}).get("token"))
        self.assertEqual(res.get("device", {}).get("device_id"), device_id)
        self.assertTrue(res.get("default_wallet_id"))

        res2 = login_v2(
            identifier=phone,
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertTrue(res2.get("auth", {}).get("token"))
        self.assertNotEqual(res2["auth"]["token"], res["auth"]["token"])  # rotation
        self.assertTrue(res2.get("default_wallet_id"))

    def test_login_by_email(self):
        email = f"u_{frappe.generate_hash(length=8)}@example.com"
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"
        register_user_v2(
            email=email,
            full_name="Email User",
            password=password,
            device={"device_id": device_id, "platform": "web"},
        )

        res = login_v2(identifier=email, password=password, device={"device_id": device_id, "platform": "web"})
        self.assertEqual(res.get("user", {}).get("email"), email)

    def test_register_response_has_token_and_wallet(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="Token User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertTrue(res.get("auth", {}).get("token"))
        self.assertTrue(res.get("default_wallet_id"))

    def test_register_user_type_is_website(self):
        # FIXED 2026-07-03: the 'Hisabi User' role is now created with desk_access=0
        # (install.ensure_roles), so a registrant stays a Website User instead of being
        # auto-promoted to System User (desk access + a license seat) on save.
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="Type User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        user = res.get("user", {}).get("name")
        user_type = frappe.get_value("User", user, "user_type")
        self.assertNotEqual(user_type, "System User")

    def test_register_clears_server_messages(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        register_user_v2(
            phone=phone,
            full_name="Msg User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        message_log = getattr(frappe.local, "message_log", None) or []
        self.assertEqual(len(message_log), 0)

    def test_me_requires_token(self):
        frappe.local.request = frappe._dict(headers={})
        res = me_v2()
        self.assertEqual(res.get("error", {}).get("code"), "UNAUTHORIZED")

    def test_devices_list_marks_current(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        token = res["auth"]["token"]
        self._set_bearer(token)
        out = devices_list()
        devices = out.get("devices") or []
        self.assertTrue(any(d.get("device_id") == device_id and d.get("is_current") for d in devices))

    def test_revoked_token_blocked_from_sync(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        token = res["auth"]["token"]

        # token works
        self._set_bearer(token)
        out = me_v2()
        self.assertEqual(out["device"]["device_id"], device_id)

        # revoke device
        device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
        device = frappe.get_doc("Hisabi Device", device_name)
        device.status = "revoked"
        device.save(ignore_permissions=True)

        self._set_bearer(token)
        resp = sync_push(device_id=device_id, wallet_id=res["default_wallet_id"], items=[])
        self.assertEqual(resp.status_code, 403)

    def test_old_token_rejected_after_rotation(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        token1 = res["auth"]["token"]

        res2 = login_v2(
            identifier=phone,
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        token2 = res2["auth"]["token"]
        self.assertNotEqual(token1, token2)

        self._set_bearer(token1)
        old = me_v2()
        self.assertEqual(old.get("error", {}).get("code"), "UNAUTHORIZED")

        self._set_bearer(token2)
        current = me_v2()
        self.assertEqual(current.get("device", {}).get("device_id"), device_id)

    def test_account_lockout_after_failures(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        # Fail 5 times -> locked. login() returns an error dict, it does not raise.
        for _ in range(5):
            res = login_v2(identifier=phone, password="wrongpass", device={"device_id": device_id, "platform": "android"})
            self.assertEqual(res.get("error", {}).get("code"), "INVALID_CREDENTIALS")

        # Correct password is now rejected because the account is locked.
        res = login_v2(identifier=phone, password=password, device={"device_id": device_id, "platform": "android"})
        self.assertIsNone(res.get("auth"))
        self.assertEqual(res.get("error", {}).get("code"), "INVALID_CREDENTIALS")

    def test_register_rejects_device_reuse(self):
        device_id = f"dev-shared-{frappe.generate_hash(length=6)}"
        password = "testpass123"

        register_user_v2(
            phone=_phone(),
            full_name="User A",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        frappe.local.response = {}
        res = register_user_v2(
            phone=_phone(),
            full_name="User B",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertEqual(res.get("error", {}).get("code"), "DEVICE_IN_USE")
        self.assertEqual(frappe.local.response.get("http_status_code"), 409)

    def test_blocked_device_cannot_login(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        device_name = frappe.get_value("Hisabi Device", {"device_id": device_id})
        device = frappe.get_doc("Hisabi Device", device_name)
        device.status = "blocked"
        device.save(ignore_permissions=True)

        frappe.local.response = {}
        res = login_v2(identifier=phone, password=password, device={"device_id": device_id, "platform": "android"})
        self.assertEqual(res.get("error", {}).get("code"), "DEVICE_BLOCKED")
        self.assertEqual(frappe.local.response.get("http_status_code"), 403)

    def test_device_reuse_same_user_ok(self):
        phone = _phone()
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        res = register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertTrue(res.get("auth", {}).get("token"))

        res2 = login_v2(
            identifier=phone,
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertTrue(res2.get("auth", {}).get("token"))

    def test_device_reuse_different_user_conflict_409(self):
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        user_a = register_user_v2(
            phone=_phone(),
            full_name="User A",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertTrue(user_a.get("auth", {}).get("token"))

        user_b_phone = _phone()
        register_user_v2(
            phone=user_b_phone,
            full_name="User B",
            password=password,
            device={"device_id": f"dev-{frappe.generate_hash(length=8)}", "platform": "android"},
        )

        frappe.local.response = {}
        res = login_v2(
            identifier=user_b_phone,
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertEqual(res.get("error", {}).get("code"), "DEVICE_IN_USE")
        self.assertEqual(frappe.local.response.get("http_status_code"), 409)
