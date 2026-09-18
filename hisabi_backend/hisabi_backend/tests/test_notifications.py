"""Tests for the user-scoped notification inbox.

These exercise the real doctype, so they run only after the pipeline migration creates
`Hisabi Notification` (the endpoints/emit helper no-op safely before then). setUp skips the module if
the table is absent so a pre-migrate CI run does not spuriously fail.
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.debts import accept_request, create_network_request
from hisabi_backend.api.v1.notifications import (
    notification_dismiss,
    notifications_list,
    notifications_mark_all_read,
    notifications_mark_read,
)
from hisabi_backend.api.v1.wallets import wallet_create, wallet_invite_create
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.notifications import create_user_notification


class TestNotifications(FrappeTestCase):
    def _new_user(self, prefix, phone=None):
        email = f"{prefix}_{frappe.generate_hash(length=6)}@example.com"
        data = {
            "doctype": "User",
            "email": email,
            "first_name": prefix,
            "last_name": "T",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }
        if phone:
            data["phone"] = phone
            data["mobile_no"] = phone
        user = frappe.get_doc(data).insert(ignore_permissions=True)
        update_password(user.name, "test12345")
        return user

    def _auth_as(self, user):
        frappe.set_user(user.name)
        device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(device_id, "android", "Pixel")
        token = device.get("device_token")
        frappe.local.request = type("o", (object,), {"headers": {"Authorization": f"Bearer {token}"}})()
        return device_id

    def setUp(self):
        if not frappe.db.table_exists("Hisabi Notification"):
            self.skipTest("Hisabi Notification not migrated yet")
        ensure_roles()
        suffix = "".join(c for c in frappe.generate_hash(length=6) if c.isdigit()).ljust(5, "1")[:5]
        self.sender_phone = f"96770{suffix}0"
        self.recipient_phone = f"96771{suffix}1"
        self.sender = self._new_user("nt_sender", self.sender_phone)
        self.recipient = self._new_user("nt_recipient", self.recipient_phone)

    def test_direct_emit_list_and_mark_read(self):
        create_user_notification(self.recipient.name, "system", "مرحبا", "اختبار")
        device = self._auth_as(self.recipient)

        rows = notifications_list(device_id=device)["notifications"]
        mine = [r for r in rows if r["title"] == "مرحبا"]
        self.assertEqual(len(mine), 1)

        notifications_mark_read(ids=[mine[0]["id"]], device_id=device)
        after = [r for r in notifications_list(device_id=device)["notifications"] if r["title"] == "مرحبا"]
        self.assertEqual(len(after), 0)  # list returns unread only

    def test_ownership_isolation(self):
        create_user_notification(self.recipient.name, "system", "خاص", "للمستلم فقط")
        target = frappe.get_all(
            "Hisabi Notification", filters={"user": self.recipient.name, "title": "خاص"}, pluck="name"
        )[0]

        # The sender must not be able to read or mark the recipient's notification.
        sender_device = self._auth_as(self.sender)
        self.assertFalse(any(r["id"] == target for r in notifications_list(device_id=sender_device)["notifications"]))
        res = notifications_mark_read(ids=[target], device_id=sender_device)
        self.assertEqual(res["updated"], 0)
        self.assertEqual(frappe.db.get_value("Hisabi Notification", target, "is_read"), 0)

    def test_dismiss_removes_from_list(self):
        create_user_notification(self.recipient.name, "system", "للحذف", "x")
        device = self._auth_as(self.recipient)
        nid = [r for r in notifications_list(device_id=device)["notifications"] if r["title"] == "للحذف"][0]["id"]
        notification_dismiss(notification_id=nid, device_id=device)
        self.assertFalse(any(r["title"] == "للحذف" for r in notifications_list(device_id=device)["notifications"]))

    def test_accept_emits_response_notification_to_sender(self):
        sender_device = self._auth_as(self.sender)
        created = create_network_request(
            to_phone=self.recipient_phone,
            payload={"direction": "owed_to_me", "currency": "SAR", "principal_amount": 100, "counterparty_name": "R"},
            device_id=sender_device,
        )
        recipient_device = self._auth_as(self.recipient)
        accept_request(request_id=created["request_id"], device_id=recipient_device)

        # Sender receives a debt_request_response notification.
        sender_device = self._auth_as(self.sender)
        rows = notifications_list(device_id=sender_device)["notifications"]
        self.assertTrue(any(r["type"] == "debt_request_response" for r in rows))

    def test_invite_emits_notification_to_registered_invitee(self):
        owner_device = self._auth_as(self.sender)
        wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=wallet_id, wallet_name="Shared", device_id=owner_device)
        wallet_invite_create(
            wallet_id=wallet_id, role_to_grant="member", target_phone=self.recipient_phone, device_id=owner_device
        )

        recipient_device = self._auth_as(self.recipient)
        rows = notifications_list(device_id=recipient_device)["notifications"]
        self.assertTrue(any(r["type"] == "invite_received" for r in rows))

    def test_mark_all_read(self):
        create_user_notification(self.recipient.name, "system", "a", "1")
        create_user_notification(self.recipient.name, "system", "b", "2")
        device = self._auth_as(self.recipient)
        self.assertGreaterEqual(len(notifications_list(device_id=device)["notifications"]), 2)
        notifications_mark_all_read(device_id=device)
        self.assertEqual(len(notifications_list(device_id=device)["notifications"]), 0)
