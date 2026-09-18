"""Concurrency regression tests for shared-wallet sync (P0.3).

Covers the exact multi-user/multi-device scenarios the architecture review flagged as having
zero coverage:
  1. Two users in one shared wallet editing the SAME record → the stale writer is safely rejected
     as a conflict (never a silent overwrite / lost update).
  2. Two devices posting transactions to the SAME account → the recalculated balance includes
     BOTH (no lost update); balance is always a full re-derivation of the committed ledger.

Note: FrappeTestCase runs each test inside a single transaction that is rolled back, so genuine
parallel two-connection racing cannot be exercised deterministically here. These tests instead
lock in the *invariants* that the FOR UPDATE + LOCK IN SHARE MODE recalc guarantees under
concurrency (re-derivation correctness + conflict-on-stale-base), which is what actually protects
the money numbers.
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils import now_datetime, now_datetime as _now
from frappe.utils.password import update_password

from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.install import ensure_roles
from hisabi_backend.hisabi_backend.tests.test_sync import sync_push


def _new_user(prefix: str) -> str:
    email = f"{prefix}_{frappe.generate_hash(length=6)}@example.com"
    user = frappe.get_doc({
        "doctype": "User", "email": email, "first_name": "Concur", "last_name": "Tester",
        "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
    }).insert(ignore_permissions=True)
    update_password(user.name, "test123")
    return user.name


def _set_token(token: str) -> None:
    frappe.local.request = type(
        "obj", (object,), {"headers": {"Authorization": f"Bearer {token}"}}
    )()


class TestSharedWalletConcurrency(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        # Owner (user A) + wallet.
        self.user_a = _new_user("concur_a")
        frappe.set_user(self.user_a)
        self.device_a = f"dev-a-{frappe.generate_hash(length=6)}"
        dev_a = register_device(self.device_a, "android", "Pixel A")
        self.token_a = dev_a.get("device_token")
        _set_token(self.token_a)
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Shared", device_id=self.device_a)

        # Second member (user B), added directly to the same wallet.
        self.user_b = _new_user("concur_b")
        frappe.set_user(self.user_b)
        self.device_b = f"dev-b-{frappe.generate_hash(length=6)}"
        dev_b = register_device(self.device_b, "android", "Pixel B")
        self.token_b = dev_b.get("device_token")

        frappe.set_user("Administrator")
        member = frappe.new_doc("Hisabi Wallet Member")
        member.wallet = self.wallet_id
        member.user = self.user_b
        member.role = "member"
        member.status = "active"
        member.joined_at = now_datetime()
        member.insert(ignore_permissions=True)

        frappe.set_user(self.user_a)
        _set_token(self.token_a)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _seed_account(self, client_id: str, opening: float = 0.0) -> None:
        resp = sync_push(
            device_id=self.device_a, wallet_id=self.wallet_id,
            items=[{
                "op_id": f"op-{client_id}", "entity_type": "Hisabi Account",
                "entity_id": client_id, "operation": "create",
                "payload": {
                    "client_id": client_id, "wallet_id": self.wallet_id,
                    "account_name": "Cash", "account_type": "cash",
                    "currency": "SAR", "opening_balance": opening,
                },
            }],
        )
        self.assertEqual(resp["results"][0]["status"], "accepted")

    # --- 1) Cross-user same-record conflict (no silent overwrite / lost update) ---
    def test_two_users_same_record_stale_write_is_conflict(self):
        # A creates a shared account (doc_version = 1).
        self._seed_account("acc-shared", opening=0)
        base_v = frappe.db.get_value("Hisabi Account", "acc-shared", "doc_version")

        # A renames it (accepted → doc_version bumps to base_v + 1).
        ra = sync_push(
            device_id=self.device_a, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-acc-a-update", "entity_type": "Hisabi Account",
                "entity_id": "acc-shared", "operation": "update", "base_version": base_v,
                "payload": {
                    "client_id": "acc-shared", "account_name": "A renamed",
                    "account_type": "cash", "currency": "SAR", "client_modified_ms": 1700000000001,
                },
            }],
        )
        self.assertEqual(ra["results"][0]["status"], "accepted")

        # B (same wallet, SECOND USER, second device) edits the same record with the now-STALE
        # base_version. It must be rejected as a conflict — never silently overwrite A's change.
        _set_token(self.token_b)
        frappe.set_user(self.user_b)
        rb = sync_push(
            device_id=self.device_b, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-acc-b-update", "entity_type": "Hisabi Account",
                "entity_id": "acc-shared", "operation": "update", "base_version": base_v,
                "payload": {
                    "client_id": "acc-shared", "account_name": "B clobber",
                    "account_type": "cash", "currency": "SAR", "client_modified_ms": 1700000000002,
                },
            }],
        )
        self.assertEqual(rb["results"][0]["status"], "conflict", "stale cross-user write must conflict")

        # A's committed value survives; B did not overwrite it.
        self.assertEqual(
            frappe.db.get_value("Hisabi Account", "acc-shared", "account_name"), "A renamed"
        )

    # --- 2) Two devices posting to the same account → no lost update in the balance ---
    def test_two_device_writes_same_account_no_lost_update(self):
        self._seed_account("acc-joint", opening=1000)

        # Device A posts an expense of 30.
        ra = sync_push(
            device_id=self.device_a, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-tx-a", "entity_type": "Hisabi Transaction",
                "entity_id": "tx-a", "operation": "create",
                "payload": {
                    "client_id": "tx-a", "wallet_id": self.wallet_id,
                    "transaction_type": "expense", "amount": 30, "currency": "SAR",
                    "account": "acc-joint", "date_time": now_datetime(),
                },
            }],
        )
        self.assertEqual(ra["results"][0]["status"], "accepted")

        # Device B (second member) posts an expense of 20 to the SAME account.
        _set_token(self.token_b)
        frappe.set_user(self.user_b)
        rb = sync_push(
            device_id=self.device_b, wallet_id=self.wallet_id,
            items=[{
                "op_id": "op-tx-b", "entity_type": "Hisabi Transaction",
                "entity_id": "tx-b", "operation": "create",
                "payload": {
                    "client_id": "tx-b", "wallet_id": self.wallet_id,
                    "transaction_type": "expense", "amount": 20, "currency": "SAR",
                    "account": "acc-joint", "date_time": now_datetime(),
                },
            }],
        )
        self.assertEqual(rb["results"][0]["status"], "accepted")

        # Balance must reflect BOTH transactions (1000 - 30 - 20 = 950): a full re-derivation of
        # the committed ledger, with neither device's write lost.
        self.assertEqual(
            frappe.db.get_value("Hisabi Account", "acc-joint", "current_balance"), 950
        )
