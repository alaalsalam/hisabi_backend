"""Tests for the consolidated allocation engine (Plan 0001 - A).

All tests skip gracefully until bench migrate adds the new columns to
Hisabi Transaction Bucket (currency, amount_base, is_manual_override, rule_used).

Covers (all require migrate):
  - Schema: new fields present in DocField table
  - Auto-allocation: creates canonical row, no legacy dual-write
  - Field values: is_manual_override defaults to 0, currency populated
  - Reports query: can read currency/amount_base from canonical
  - Edge case: zero-amount transaction doesn't raise
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.domain.allocation_engine import apply_auto_allocations
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user

_SKIP_MSG = "Hisabi Transaction Bucket schema not yet migrated (bench migrate required)"


def _schema_migrated() -> bool:
    """True once bench migrate has added the currency field to the canonical table."""
    return bool(
        frappe.db.get_value(
            "DocField",
            {"parent": "Hisabi Transaction Bucket", "fieldname": "currency"},
            "name",
        )
    )


def _make_authed_user(prefix: str):
    email = f"{prefix}_{frappe.generate_hash(length=6)}@example.com"
    user = frappe.get_doc(
        {
            "doctype": "User", "email": email,
            "first_name": prefix, "last_name": "AllocTest",
            "send_welcome_email": 0, "roles": [{"role": "Hisabi User"}],
        }
    ).insert(ignore_permissions=True)
    update_password(user.name, "test12345")
    frappe.set_user(user.name)
    device_id = f"device-{frappe.generate_hash(length=6)}"
    wallet_id = ensure_default_wallet_for_user(user.name, device_id=device_id)
    token, _dev = issue_device_token_for_device(
        user=user.name, device_id=device_id, platform="android",
        device_name="Pixel 8", wallet_id=wallet_id,
    )
    frappe.local.request = type(
        "obj", (object,), {"headers": {"Authorization": f"Bearer {token}"}}
    )()
    return user, device_id, wallet_id, token


class TestAllocationConsolidated(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        if not _schema_migrated():
            self.skipTest(_SKIP_MSG)
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("alloc_canon")

        self.account = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "client_id": f"acc-canon-{frappe.generate_hash(6)}",
                "account_name": "Main Cash", "account_type": "cash",
                "currency": "SAR", "opening_balance": 0,
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.bucket = frappe.get_doc(
            {
                "doctype": "Hisabi Bucket",
                "client_id": f"bucket-canon-{frappe.generate_hash(6)}",
                "title": "Living Expenses",
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        self.rule = frappe.get_doc(
            {
                "doctype": "Hisabi Allocation Rule",
                "client_id": f"rule-canon-{frappe.generate_hash(6)}",
                "rule_name": "Default", "scope_type": "global",
                "is_default": 1, "active": 1,
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "Hisabi Allocation Rule Line",
                "client_id": f"line-canon-{frappe.generate_hash(6)}",
                "rule": self.rule.name, "bucket": self.bucket.name,
                "percent": 100,
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _tx(self, amount=500.0, currency="SAR"):
        # Must be "income" — allocation engine only creates bucket rows for income transactions.
        return frappe.get_doc(
            {
                "doctype": "Hisabi Transaction",
                "client_id": f"tx-canon-{frappe.generate_hash(8)}",
                "amount": amount, "currency": currency,
                "transaction_type": "income",
                "date_time": frappe.utils.now_datetime(),
                "account": self.account.name,
                "user": self.user.name, "wallet_id": self.wallet_id,
            }
        ).insert(ignore_permissions=True)

    def test_bucket_has_new_fields(self):
        meta = frappe.get_meta("Hisabi Transaction Bucket")
        names = {f.fieldname for f in meta.fields}
        for f in ("currency", "amount_base", "is_manual_override", "rule_used"):
            self.assertIn(f, names, f"Missing field: {f}")

    def test_auto_allocation_creates_canonical_row(self):
        tx = self._tx(200.0)
        apply_auto_allocations(tx)
        self.assertGreater(
            frappe.db.count("Hisabi Transaction Bucket",
                            {"transaction_id": tx.name, "wallet_id": self.wallet_id, "is_deleted": 0}),
            0,
        )

    def test_no_legacy_dual_write(self):
        if not frappe.db.exists("DocType", "Hisabi Transaction Allocation"):
            return
        tx = self._tx(300.0)
        before = frappe.db.count("Hisabi Transaction Allocation", {"transaction": tx.name})
        apply_auto_allocations(tx)
        after = frappe.db.count("Hisabi Transaction Allocation", {"transaction": tx.name})
        self.assertEqual(before, after)

    def test_manual_override_default_zero(self):
        tx = self._tx(400.0)
        apply_auto_allocations(tx)
        buckets = frappe.get_all(
            "Hisabi Transaction Bucket",
            {"transaction_id": tx.name, "wallet_id": self.wallet_id, "is_deleted": 0},
            ["is_manual_override"],
        )
        for b in buckets:
            self.assertEqual(int(b.is_manual_override or 0), 0)

    def test_currency_populated(self):
        tx = self._tx(150.0, "USD")
        apply_auto_allocations(tx)
        buckets = frappe.get_all(
            "Hisabi Transaction Bucket",
            {"transaction_id": tx.name, "wallet_id": self.wallet_id, "is_deleted": 0},
            ["currency"],
        )
        for b in buckets:
            self.assertIsNotNone(b.currency)

    def test_reports_read_canonical(self):
        tx = self._tx(500.0)
        apply_auto_allocations(tx)
        rows = frappe.db.sql(
            "SELECT transaction_id, amount, currency, amount_base "
            "FROM `tabHisabi Transaction Bucket` "
            "WHERE wallet_id=%s AND transaction_id=%s AND is_deleted=0",
            (self.wallet_id, tx.name), as_dict=True,
        )
        self.assertGreater(len(rows), 0)

    def test_zero_amount_no_raise(self):
        tx = self._tx(0.0)
        try:
            apply_auto_allocations(tx)
        except Exception as exc:
            self.fail(f"apply_auto_allocations raised on zero-amount: {exc}")
