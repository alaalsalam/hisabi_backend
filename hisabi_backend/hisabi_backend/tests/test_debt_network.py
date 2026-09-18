import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1.debts import (
    accept_request,
    create_network_request,
    debt_requests_list,
    decline_request,
    user_lookup_by_phone,
)
from hisabi_backend.install import ensure_roles


class TestDebtNetwork(FrappeTestCase):
    def _new_user(self, prefix: str, phone: str = None) -> frappe.model.document.Document:
        email = f"{prefix}_{frappe.generate_hash(length=6)}@example.com"
        data = {
            "doctype": "User",
            "email": email,
            "first_name": prefix,
            "last_name": "Tester",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }
        if phone:
            data["phone"] = phone
            data["mobile_no"] = phone
        user = frappe.get_doc(data).insert(ignore_permissions=True)
        update_password(user.name, "test12345")
        return user

    def _auth_as(self, user: frappe.model.document.Document) -> str:
        frappe.set_user(user.name)
        device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(device_id, "android", "Pixel 8")
        token = device.get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {token}"}})()
        return device_id

    def setUp(self):
        ensure_roles()
        suffix = frappe.generate_hash(length=5)
        digits = "".join(c for c in suffix if c.isdigit()).ljust(5, "1")[:5]
        self.sender_phone = f"96770{digits}0"
        self.recipient_phone = f"96771{digits}1"
        self.sender = self._new_user("dn_sender", self.sender_phone)
        self.recipient = self._new_user("dn_recipient", self.recipient_phone)

    def _send_request(self, device_id, direction="owed_to_me", amount=250.0, name="Recipient"):
        return create_network_request(
            to_phone=self.recipient_phone,
            payload={
                "direction": direction,
                "currency": "SAR",
                "principal_amount": amount,
                "note": "lunch",
                "counterparty_name": name,
            },
            device_id=device_id,
        )

    # ---- lookup ----------------------------------------------------------------
    def test_lookup_found_matches_on_digits_even_with_plus_prefix(self):
        device_id = self._auth_as(self.sender)
        # Stored as bare digits; a '+'-prefixed query must still match.
        res = user_lookup_by_phone(phone=f"+{self.recipient_phone}", device_id=device_id)
        self.assertTrue(res["found"])
        self.assertEqual(res["display_name"], self.recipient.full_name)

    def test_lookup_not_found_for_unknown_phone(self):
        device_id = self._auth_as(self.sender)
        res = user_lookup_by_phone(phone="967999888777", device_id=device_id)
        self.assertFalse(res["found"])
        self.assertIsNone(res["display_name"])

    def test_lookup_requires_auth(self):
        frappe.set_user("Guest")
        frappe.local.request = type("obj", (object,), {"headers": {}})()
        with self.assertRaises(Exception):
            user_lookup_by_phone(phone=self.recipient_phone)

    # ---- create validation -----------------------------------------------------
    def test_create_rejects_non_positive_amount(self):
        device_id = self._auth_as(self.sender)
        with self.assertRaises(frappe.ValidationError):
            self._send_request(device_id, amount=0)

    def test_create_rejects_bad_direction(self):
        device_id = self._auth_as(self.sender)
        with self.assertRaises(frappe.ValidationError):
            create_network_request(
                to_phone=self.recipient_phone,
                payload={"direction": "sideways", "principal_amount": 10},
                device_id=device_id,
            )

    # ---- list ------------------------------------------------------------------
    def test_incoming_and_outgoing_listing(self):
        sender_device = self._auth_as(self.sender)
        created = self._send_request(sender_device)
        outgoing = debt_requests_list(direction="outgoing", device_id=sender_device)["requests"]
        self.assertTrue(any(r["request_id"] == created["request_id"] for r in outgoing))

        recipient_device = self._auth_as(self.recipient)
        incoming = debt_requests_list(direction="incoming", device_id=recipient_device)["requests"]
        match = [r for r in incoming if r["request_id"] == created["request_id"]]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["from_name"], self.sender.full_name)
        self.assertEqual(match[0]["status"], "pending")

    # ---- accept ----------------------------------------------------------------
    def test_accept_creates_mirror_debt_with_inverted_direction_and_sender_counterparty(self):
        sender_device = self._auth_as(self.sender)
        created = self._send_request(sender_device, direction="owed_to_me", amount=250.0)

        recipient_device = self._auth_as(self.recipient)
        result = accept_request(request_id=created["request_id"], device_id=recipient_device)
        self.assertEqual(result["status"], "accepted")

        debt = frappe.get_doc("Hisabi Debt", result["debt"])
        self.assertEqual(debt.user, self.recipient.name)
        # Sender said "owed_to_me" (recipient owes sender) -> recipient's debt is "owe".
        self.assertEqual(debt.direction, "owe")
        self.assertEqual(debt.counterparty_name, self.sender.full_name)
        self.assertEqual(debt.counterparty_phone, self.sender_phone)
        self.assertEqual(debt.principal_amount, 250.0)
        self.assertEqual(debt.remaining_amount, 250.0)
        self.assertTrue(debt.wallet_id)
        self.assertEqual(debt.counterparty_type, "user_phone")

        # Idempotent: a second accept must not create a duplicate debt.
        again = accept_request(request_id=created["request_id"], device_id=recipient_device)
        self.assertTrue(again.get("already"))

    def test_creator_cannot_accept_own_request(self):
        sender_device = self._auth_as(self.sender)
        created = self._send_request(sender_device)
        # Sender re-auth and tries to accept the request they created.
        sender_device = self._auth_as(self.sender)
        with self.assertRaises(frappe.PermissionError):
            accept_request(request_id=created["request_id"], device_id=sender_device)

    def test_phoneless_stranger_cannot_accept(self):
        sender_device = self._auth_as(self.sender)
        created = self._send_request(sender_device)
        # A user with NO phone must not slip through the authorization check.
        stranger = self._new_user("dn_stranger")
        stranger_device = self._auth_as(stranger)
        with self.assertRaises(frappe.PermissionError):
            accept_request(request_id=created["request_id"], device_id=stranger_device)

    # ---- decline ---------------------------------------------------------------
    def test_recipient_declines_then_accept_is_blocked(self):
        sender_device = self._auth_as(self.sender)
        created = self._send_request(sender_device)

        recipient_device = self._auth_as(self.recipient)
        res = decline_request(request_id=created["request_id"], device_id=recipient_device)
        self.assertEqual(res["status"], "declined")
        self.assertEqual(frappe.db.get_value("Hisabi Debt Request", created["request_id"], "status"), "rejected")

        with self.assertRaises(frappe.ValidationError):
            accept_request(request_id=created["request_id"], device_id=recipient_device)
