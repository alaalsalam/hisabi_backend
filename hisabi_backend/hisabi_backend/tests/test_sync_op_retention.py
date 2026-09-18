"""Retention job for the sync idempotency ledger (P3.2)."""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils import add_days, now_datetime

from hisabi_backend.tasks import prune_sync_ops


class TestSyncOpRetention(FrappeTestCase):
    def _make_op(self, op_id: str, *, age_days: int) -> str:
        doc = frappe.get_doc({
            "doctype": "Hisabi Sync Op",
            "user": "Administrator",
            "device_id": f"dev-{frappe.generate_hash(6)}",
            "op_id": op_id,
            "status": "accepted",
        }).insert(ignore_permissions=True)
        # Backdate `creation` directly (Frappe manages it on insert) to simulate an aged row.
        frappe.db.sql(
            "UPDATE `tabHisabi Sync Op` SET creation=%s WHERE name=%s",
            (add_days(now_datetime(), -age_days), doc.name),
        )
        return doc.name

    def test_prune_removes_old_keeps_recent(self):
        old = self._make_op(f"old-{frappe.generate_hash(6)}", age_days=120)
        recent = self._make_op(f"new-{frappe.generate_hash(6)}", age_days=5)

        # commit=False keeps everything inside the test transaction (rolled back after the test).
        removed = prune_sync_ops(retention_days=90, commit=False)

        self.assertGreaterEqual(removed, 1)
        self.assertFalse(frappe.db.exists("Hisabi Sync Op", old), "row older than retention must be pruned")
        self.assertTrue(frappe.db.exists("Hisabi Sync Op", recent), "row within retention must be kept")
