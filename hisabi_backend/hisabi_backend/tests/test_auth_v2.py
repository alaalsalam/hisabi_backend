import frappe
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1.auth_v2 import login as login_v2
from hisabi_backend.api.v1.auth_v2 import me as me_v2
from hisabi_backend.api.v1.auth_v2 import register_user as register_user_v2
from hisabi_backend.api.v1.devices import devices_list
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


class TestAuthV2(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def _set_bearer(self, token: str):
        frappe.local.request = frappe._dict(headers={"Authorization": f"Bearer {token}"}, remote_addr="1.2.3.4")

    def test_register_and_login_by_phone(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
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

        res2 = login_v2(
            identifier=phone,
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )
        self.assertTrue(res2.get("auth", {}).get("token"))
        self.assertNotEqual(res2["auth"]["token"], res["auth"]["token"])  # rotation

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

    def test_me_requires_token(self):
        frappe.local.request = frappe._dict(headers={})
        with self.assertRaises(frappe.AuthenticationError):
            me_v2()

    def test_devices_list_marks_current(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
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
        phone = f"+1555{frappe.generate_hash(length=6)}"
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
        with self.assertRaises(frappe.PermissionError):
            sync_push(device_id=device_id, wallet_id=res["default_wallet_id"], items=[])

    def test_old_token_rejected_after_rotation(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
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
        with self.assertRaises(frappe.AuthenticationError):
            me_v2()

        self._set_bearer(token2)
        me_v2()

    def test_account_lockout_after_failures(self):
        phone = f"+1555{frappe.generate_hash(length=6)}"
        password = "testpass123"
        device_id = f"dev-{frappe.generate_hash(length=8)}"

        register_user_v2(
            phone=phone,
            full_name="User",
            password=password,
            device={"device_id": device_id, "platform": "android"},
        )

        # Fail 5 times -> locked
        for _ in range(5):
            with self.assertRaises(Exception):
                login_v2(identifier=phone, password="wrongpass", device={"device_id": device_id, "platform": "android"})

        with self.assertRaises(frappe.AuthenticationError):
            login_v2(identifier=phone, password=password, device={"device_id": device_id, "platform": "android"})
