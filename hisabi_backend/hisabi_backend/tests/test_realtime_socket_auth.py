"""Socket.io auth hook: the before_request bearer hook must authenticate a Hisabi device token on
the `frappe.realtime.get_user_info` probe (so the SPA's token-auth socket resolves the real user),
without breaking cookie/Desk realtime and without throwing on a bad/absent token.
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.bearer_auth import authenticate_request


def _req(headers):
    return type("obj", (object,), {"method": "GET", "path": "/api/method/frappe.realtime.get_user_info", "headers": headers})()


class TestRealtimeSocketAuth(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"rt_{frappe.generate_hash(6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "RT", "last_name": "Sock",
            "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user.name
        self.device_id = f"dev-{frappe.generate_hash(6)}"
        self.token = register_device(self.device_id, "android", "P").get("device_token")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_get_user_info_authenticates_valid_device_token(self):
        frappe.set_user("Guest")
        frappe.form_dict = frappe._dict({"cmd": "frappe.realtime.get_user_info"})
        frappe.local.request = _req({"Authorization": f"Bearer {self.token}"})
        authenticate_request()
        self.assertEqual(frappe.session.user, self.user, "realtime probe must resolve the token's user")

    def test_get_user_info_bad_token_does_not_throw_stays_guest(self):
        frappe.set_user("Guest")
        frappe.form_dict = frappe._dict({"cmd": "frappe.realtime.get_user_info"})
        frappe.local.request = _req({"Authorization": "Bearer hisabi_totally_invalid_token"})
        # Best-effort: must NOT raise (cookie/Desk realtime clients must keep working).
        authenticate_request()
        self.assertEqual(frappe.session.user, "Guest", "a bad token must fall through to Guest, not error")

    def test_get_user_info_no_auth_header_is_noop(self):
        frappe.set_user("Guest")
        frappe.form_dict = frappe._dict({"cmd": "frappe.realtime.get_user_info"})
        frappe.local.request = _req({})  # cookie-based (Desk) realtime — no Authorization
        authenticate_request()  # must not raise, must not touch the user
        self.assertEqual(frappe.session.user, "Guest")
