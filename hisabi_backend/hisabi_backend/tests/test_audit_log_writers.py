"""Regression tests for the system-written Hisabi Audit Log writers.

Both writers used to fail silently (only an Error Log row was left behind):
- security events for a NON-member were rejected by the wallet-scope doc event, so denied
  access attempts were never audited;
- sync audit payloads containing datetimes were not JSON serializable.
"""

import datetime

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.sync import _write_audit_log
from hisabi_backend.api.v1.wallets import wallet_create
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.audit_security import audit_security_event


class TestAuditLogWriters(FrappeTestCase):
    def _new_user(self, prefix):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": f"{prefix}_{frappe.generate_hash(length=6)}@example.com",
                "first_name": prefix,
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test12345")
        return user

    def _auth_as(self, user):
        frappe.set_user(user.name)
        device_id = f"device-{frappe.generate_hash(length=6)}"
        token = register_device(device_id, "android", "Pixel").get("device_token")
        frappe.local.request = type("o", (object,), {"headers": {"Authorization": f"Bearer {token}"}})()
        return device_id

    def setUp(self):
        ensure_roles()
        self.owner = self._new_user("audit_owner")
        self.outsider = self._new_user("audit_outsider")
        self.owner_device = self._auth_as(self.owner)
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Audit Wallet", device_id=self.owner_device)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _audit_rows(self, **filters):
        return frappe.get_all("Hisabi Audit Log", filters={"wallet_id": self.wallet_id, **filters}, pluck="name")

    def test_security_event_for_non_member_is_recorded(self):
        self._auth_as(self.outsider)
        event = f"wallet_access_denied_{frappe.generate_hash(length=6)}"

        audit_security_event(event, user=self.outsider.name, payload={"wallet_id": self.wallet_id})

        frappe.set_user("Administrator")
        rows = self._audit_rows(event_type=event, user=self.outsider.name)
        self.assertEqual(len(rows), 1)

    def test_sync_audit_with_datetime_payload_is_recorded(self):
        op_id = f"op-{frappe.generate_hash(length=8)}"

        _write_audit_log(
            user=self.owner.name,
            device_id=self.owner_device,
            wallet_id=self.wallet_id,
            op_id=op_id,
            entity_type="Hisabi Transaction",
            entity_client_id="tx-audit",
            status="accepted",
            payload={"date_time": datetime.datetime(2026, 11, 9, 10, 30)},
        )

        frappe.set_user("Administrator")
        rows = self._audit_rows(op_id=op_id)
        self.assertEqual(len(rows), 1)
        payload = frappe.db.get_value("Hisabi Audit Log", rows[0], "payload_json")
        self.assertIn("2026-11-09", payload)

    def test_non_member_still_cannot_write_wallet_audit_log_directly(self):
        # The bypass is limited to the server-side writers; a user-driven insert stays scoped.
        self._auth_as(self.outsider)
        doc = frappe.get_doc(
            {
                "doctype": "Hisabi Audit Log",
                "user": self.outsider.name,
                "wallet_id": self.wallet_id,
                "status": "accepted",
                "payload_json": "{}",
            }
        )
        with self.assertRaises(frappe.PermissionError):
            doc.insert(ignore_permissions=True)
