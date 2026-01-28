# Copyright (c) 2026, alaalsalam and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.install import ensure_roles


class TestHisabiDevice(FrappeTestCase):
    def setUp(self):
        ensure_roles()

    def test_register_device(self):
        email = f"device_test_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Device",
            "last_name": "Tester",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)

        update_password(user.name, "test123")
        frappe.set_user(user.name)

        response = register_device("device-001", "android", "Pixel 8")

        self.assertEqual(response.get("device_id"), "device-001")
        self.assertEqual(response.get("status"), "active")

        token = response.get("device_token")
        self.assertTrue(token)

        device_name = frappe.get_value("Hisabi Device", {"device_id": "device-001"})
        device_doc = frappe.get_doc("Hisabi Device", device_name)
        self.assertTrue(device_doc.device_token_hash)
        self.assertNotEqual(device_doc.device_token_hash, token)
