"""Tests for Import Session API endpoints (Plan 0001 - B2).

All tests skip gracefully when the Hisabi Import Session table hasn't
been created by bench migrate yet. After migration, 15 tests should pass.

Covers:
  - create_session: happy path, idempotent, empty rows, permission check
  - get_sessions + get_session: list, full rows, cross-wallet denied, not-found
  - confirm_session: all rows, selected rows, idempotent, blocks on cancelled
  - cancel_session: pending→cancelled, blocks on confirmed
"""

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user

_SKIP_MSG = "Hisabi Import Session table not yet created (bench migrate required)"

_SAMPLE_ROWS = [
    {
        "row_index": 0, "raw_text": "Starbucks 45 SAR", "date": "2026-06-01",
        "description": "Starbucks", "amount": 45.0, "currency": "SAR",
        "transaction_type": "expense", "category": "Food", "ai_confidence": 0.92,
    },
    {
        "row_index": 1, "raw_text": "Salary 8000 SAR", "date": "2026-06-01",
        "description": "Monthly salary", "amount": 8000.0, "currency": "SAR",
        "transaction_type": "income", "category": "Salary", "ai_confidence": 0.98,
    },
    {
        "row_index": 2, "raw_text": "Amazon 120 SAR", "date": "2026-06-02",
        "description": "Amazon purchase", "amount": 120.0, "currency": "SAR",
        "transaction_type": "expense", "category": "Shopping", "ai_confidence": 0.85,
    },
]


def _make_authed_user(prefix: str):
    email = f"{prefix}_{frappe.generate_hash(length=6)}@example.com"
    user = frappe.get_doc(
        {
            "doctype": "User", "email": email,
            "first_name": prefix, "last_name": "ImportTest",
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


def _create(wallet_id, device_id, **kwargs):
    from hisabi_backend.api.v1.import_session import create_session
    return create_session(wallet_id=wallet_id, device_id=device_id, **kwargs)


class TestImportSessionCreate(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        if not frappe.db.table_exists("Hisabi Import Session"):
            self.skipTest(_SKIP_MSG)
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("imp_create")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_create_session_returns_session_id(self):
        result = _create(self.wallet_id, self.device_id, source_type="csv",
                         file_name="bank.csv", rows=_SAMPLE_ROWS)
        self.assertEqual(result["status"], "ok")
        self.assertIn("session_id", result)
        self.assertEqual(result["total_rows"], 3)

    def test_create_session_persists_rows(self):
        result = _create(self.wallet_id, self.device_id, rows=_SAMPLE_ROWS)
        session = frappe.get_doc("Hisabi Import Session", result["session_id"])
        self.assertEqual(len(session.import_rows), 3)
        self.assertEqual(session.status, "pending")

    def test_create_session_idempotent(self):
        cid = f"imp-{frappe.generate_hash(8)}"
        r1 = _create(self.wallet_id, self.device_id, rows=_SAMPLE_ROWS[:1], client_id=cid)
        r2 = _create(self.wallet_id, self.device_id, rows=_SAMPLE_ROWS[:1], client_id=cid)
        self.assertEqual(r1["session_id"], r2["session_id"])
        self.assertTrue(r2.get("already_exists"))

    def test_create_session_empty_rows(self):
        result = _create(self.wallet_id, self.device_id, rows=[])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_rows"], 0)

    def test_create_session_permission(self):
        _other, _dev, other_wallet, _tok = _make_authed_user("imp_perm")
        frappe.set_user(self.user.name)
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.token}"}}
        )()
        with self.assertRaises(frappe.PermissionError):
            _create(other_wallet, self.device_id, rows=[])


class TestImportSessionRead(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        if not frappe.db.table_exists("Hisabi Import Session"):
            self.skipTest(_SKIP_MSG)
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("imp_read")
        r = _create(self.wallet_id, self.device_id, source_type="pdf",
                    file_name="stmt.pdf", rows=_SAMPLE_ROWS)
        self.session_id = r["session_id"]

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_get_sessions_list(self):
        from hisabi_backend.api.v1.import_session import get_sessions
        result = get_sessions(wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertIn(self.session_id, [s["name"] for s in result["sessions"]])

    def test_get_session_rows(self):
        from hisabi_backend.api.v1.import_session import get_session
        result = get_session(session_id=self.session_id, wallet_id=self.wallet_id,
                             device_id=self.device_id)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["session"]["status"], "pending")

    def test_get_session_cross_wallet_denied(self):
        from hisabi_backend.api.v1.import_session import get_session
        _other, other_dev, other_wallet, other_tok = _make_authed_user("imp_cross")
        frappe.set_user(_other.name)
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {other_tok}"}}
        )()
        with self.assertRaises(frappe.PermissionError):
            get_session(session_id=self.session_id, wallet_id=self.wallet_id,
                        device_id=other_dev)

    def test_get_session_not_found(self):
        from hisabi_backend.api.v1.import_session import get_session
        with self.assertRaises(frappe.DoesNotExistError):
            get_session(session_id="nonexistent-session",
                        wallet_id=self.wallet_id, device_id=self.device_id)


class TestImportSessionConfirmCancel(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        if not frappe.db.table_exists("Hisabi Import Session"):
            self.skipTest(_SKIP_MSG)
        self.user, self.device_id, self.wallet_id, self.token = _make_authed_user("imp_cc")

    def tearDown(self):
        frappe.set_user("Administrator")

    def _sid(self):
        return _create(self.wallet_id, self.device_id, rows=_SAMPLE_ROWS)["session_id"]

    def test_confirm_all(self):
        from hisabi_backend.api.v1.import_session import confirm_session
        sid = self._sid()
        r = confirm_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertEqual(r["imported"], 3)
        self.assertEqual(r["skipped"], 0)
        self.assertEqual(frappe.db.get_value("Hisabi Import Session", sid, "status"), "confirmed")

    def test_confirm_selected(self):
        from hisabi_backend.api.v1.import_session import confirm_session
        sid = self._sid()
        r = confirm_session(session_id=sid, wallet_id=self.wallet_id,
                            selected_row_indices=[0, 2], device_id=self.device_id)
        self.assertEqual(r["imported"], 2)
        self.assertEqual(r["skipped"], 1)

    def test_confirm_idempotent(self):
        from hisabi_backend.api.v1.import_session import confirm_session
        sid = self._sid()
        confirm_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
        r = confirm_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertTrue(r.get("already_confirmed"))

    def test_cancel_pending(self):
        from hisabi_backend.api.v1.import_session import cancel_session
        sid = self._sid()
        r = cancel_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(frappe.db.get_value("Hisabi Import Session", sid, "status"), "cancelled")

    def test_cancel_confirmed_raises(self):
        from hisabi_backend.api.v1.import_session import confirm_session, cancel_session
        sid = self._sid()
        confirm_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
        with self.assertRaises(frappe.ValidationError):
            cancel_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)

    def test_confirm_cancelled_raises(self):
        from hisabi_backend.api.v1.import_session import confirm_session, cancel_session
        sid = self._sid()
        cancel_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
        with self.assertRaises(frappe.ValidationError):
            confirm_session(session_id=sid, wallet_id=self.wallet_id, device_id=self.device_id)
