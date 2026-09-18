"""Field-level conflict resolution tests (P2).

A base_version mismatch should only surface a conflict when the fields being changed actually
clash with the server's newer version. Idempotent edits and field-disjoint edits (when the client
sends `base_values`) are safely merged instead of throwing a false conflict at the user.
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.install import ensure_roles
from hisabi_backend.hisabi_backend.tests.test_sync import sync_push


class TestFieldLevelMerge(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"flm_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "FLM", "last_name": "T",
            "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.device_id = f"dev-{frappe.generate_hash(length=6)}"
        dev = register_device(self.device_id, "android", "Pixel")
        self.token = dev.get("device_token")
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.token}"}}
        )()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="FLM Wallet", device_id=self.device_id)
        # sync_push commits, so state persists across test methods — use a unique account per test.
        self.acc_id = f"acc-{frappe.generate_hash(length=8)}"

    def tearDown(self):
        frappe.set_user("Administrator")

    def _create_account(self):
        r = sync_push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[{
                "op_id": f"op-{frappe.generate_hash(6)}", "entity_type": "Hisabi Account",
                "entity_id": self.acc_id, "operation": "create",
                "payload": {
                    "client_id": self.acc_id, "wallet_id": self.wallet_id,
                    "account_name": "Cash", "account_type": "cash",
                    "currency": "SAR", "opening_balance": 0,
                },
            }],
        )
        self.assertEqual(r["results"][0]["status"], "accepted")
        return frappe.db.get_value("Hisabi Account", self.acc_id, "doc_version")  # = 1

    def _update(self, *, op, base_version, payload, base_values=None):
        payload = {"client_id": self.acc_id, **payload}
        item = {
            "op_id": op, "entity_type": "Hisabi Account", "entity_id": self.acc_id,
            "operation": "update", "base_version": base_version, "payload": payload,
        }
        if base_values is not None:
            item["base_values"] = base_values
        return sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=[item])["results"][0]

    def _rename_to(self, name, base_version):
        return self._update(
            op=f"op-rename-{frappe.generate_hash(6)}", base_version=base_version,
            payload={"account_name": name, "account_type": "cash",
                     "currency": "SAR", "client_modified_ms": 1700000000000},
        )

    def test_disjoint_field_edit_with_base_values_auto_merges(self):
        base_v = self._create_account()
        # Writer 1 renames the account (accepted → v2).
        self.assertEqual(self._rename_to("Renamed", base_v)["status"], "accepted")
        # Writer 2 (stale base_v) changes a DIFFERENT field (account_type) and supplies base_values.
        res = self._update(
            op="op-b-type", base_version=base_v,
            payload={"account_type": "bank", "currency": "SAR", "client_modified_ms": 1700000000005},
            base_values={"account_type": "cash"},
        )
        self.assertEqual(res["status"], "accepted", "disjoint field edit must auto-merge, not conflict")
        # Both changes survive: writer 1's rename AND writer 2's type change.
        self.assertEqual(frappe.db.get_value("Hisabi Account", self.acc_id, "account_name"), "Renamed")
        self.assertEqual(frappe.db.get_value("Hisabi Account", self.acc_id, "account_type"), "bank")

    def test_same_field_edit_still_conflicts(self):
        base_v = self._create_account()
        self.assertEqual(self._rename_to("A-name", base_v)["status"], "accepted")
        # Writer 2 edits the SAME field (account_name) that writer 1 already changed → real conflict.
        res = self._update(
            op="op-b-name", base_version=base_v,
            payload={"account_name": "B-name", "account_type": "cash", "currency": "SAR", "client_modified_ms": 1700000000006},
            base_values={"account_name": "Cash"},
        )
        self.assertEqual(res["status"], "conflict", "same-field concurrent edit must still conflict")
        self.assertEqual(frappe.db.get_value("Hisabi Account", self.acc_id, "account_name"), "A-name")

    def test_idempotent_same_value_edit_auto_accepts(self):
        base_v = self._create_account()
        self.assertEqual(self._rename_to("Final", base_v)["status"], "accepted")
        # Writer 2 (stale base_v, NO base_values) pushes the SAME value the server already holds.
        res = self._update(
            op="op-b-dup", base_version=base_v,
            payload={"account_name": "Final", "account_type": "cash", "currency": "SAR", "client_modified_ms": 1700000000007},
        )
        self.assertEqual(res["status"], "accepted", "idempotent no-op edit must not be a false conflict")

    def test_differing_field_without_base_values_still_conflicts(self):
        # Backward-compatible safety: a stale client that changes a field to a NEW value without
        # sending base_values is still treated as a conflict (no silent overwrite).
        base_v = self._create_account()
        self.assertEqual(self._rename_to("Server-name", base_v)["status"], "accepted")
        res = self._update(
            op="op-b-nobv", base_version=base_v,
            payload={"account_name": "Client-name", "account_type": "cash", "currency": "SAR", "client_modified_ms": 1700000000008},
        )
        self.assertEqual(res["status"], "conflict")
        self.assertEqual(frappe.db.get_value("Hisabi Account", self.acc_id, "account_name"), "Server-name")
