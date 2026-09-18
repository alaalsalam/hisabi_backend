"""Regression tests for sync-integrity fixes (2026-07-04).

- B3: an update carrying is_deleted=1 must STAY deleted (the write path previously reset it to 0
  via apply_soft_delete, silently resurrecting the record on the next pull).
- B2: a push that recomputes an account balance to the SAME value must NOT bump the account's
  doc_version (an unconditional save caused spurious pull traffic + false base_version conflicts).
"""

import json

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.sync import sync_push as _sync_push
from hisabi_backend.install import ensure_roles


def _push(**kwargs):
    r = _sync_push(**kwargs)
    if isinstance(r, dict):
        return r
    if hasattr(r, "get_data"):
        return json.loads(r.get_data(as_text=True) or "{}").get("message", {})
    return r


class TestSyncIntegrityRegression(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"syncreg_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User", "email": email, "first_name": "SyncReg", "last_name": "T",
                "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(self.device_id, "android", "Pixel 8")
        self.token = device.get("device_token")
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.token}"}}
        )()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Reg Wallet", device_id=self.device_id)

    def _create_account(self, cid, opening=100):
        return _push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[{
                "op_id": f"op-{cid}", "entity_type": "Hisabi Account", "entity_id": cid,
                "operation": "create",
                "payload": {
                    "client_id": cid, "account_name": "Cash", "account_type": "cash",
                    "currency": "SAR", "opening_balance": opening, "client_modified_ms": 1700000000000,
                },
            }],
        )

    def test_soft_delete_via_update_payload_persists(self):
        created = self._create_account("acc-del")
        base_version = int(created["results"][0]["doc_version"] or 0)
        r = _push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-del-upd", "entity_type": "Hisabi Account", "entity_id": "acc-del",
                "operation": "update", "base_version": base_version,
                "payload": {
                    "client_id": "acc-del", "account_name": "Cash", "account_type": "cash",
                    "currency": "SAR", "is_deleted": 1, "client_modified_ms": 1700000000002,
                },
            }],
        )
        self.assertEqual(r["results"][0]["status"], "accepted")
        acc = frappe.get_doc("Hisabi Account", "acc-del")
        self.assertEqual(int(acc.is_deleted or 0), 1, "record must stay soft-deleted, not resurrected")
        self.assertTrue(acc.deleted_at)

    def test_unchanged_balance_does_not_bump_account_version(self):
        self._create_account("acc-nb")
        tx_created = _push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-tx-nb", "entity_type": "Hisabi Transaction", "entity_id": "tx-nb",
                "operation": "create",
                "payload": {
                    "client_id": "tx-nb", "transaction_type": "expense", "date_time": now_datetime(),
                    "amount": 40, "currency": "SAR", "account": "acc-nb", "client_modified_ms": 1700000000003,
                },
            }],
        )
        tx_base_version = int(tx_created["results"][0]["doc_version"] or 0)
        v_before = frappe.db.get_value("Hisabi Account", "acc-nb", "doc_version")
        # Update the transaction keeping the SAME amount (note-only change) → balance stays 60.
        _push(
            device_id=self.device_id, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-tx-nb2", "entity_type": "Hisabi Transaction", "entity_id": "tx-nb",
                "operation": "update", "base_version": tx_base_version,
                "payload": {
                    "client_id": "tx-nb", "transaction_type": "expense", "date_time": now_datetime(),
                    "amount": 40, "currency": "SAR", "account": "acc-nb", "note": "unchanged-amount",
                    "client_modified_ms": 1700000000004,
                },
            }],
        )
        v_after = frappe.db.get_value("Hisabi Account", "acc-nb", "doc_version")
        self.assertEqual(v_after, v_before, "unchanged balance must not bump account doc_version")
