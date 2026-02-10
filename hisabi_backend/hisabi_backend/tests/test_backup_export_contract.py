import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.backup import export as backup_export
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBackupExportContract(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"backup_export_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Backup",
                "last_name": "Export",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123A!")
        frappe.set_user(user.name)
        self.user = user

        self.device_id = f"device-{frappe.generate_hash(length=8)}"
        self.wallet_id = ensure_default_wallet_for_user(self.user.name, device_id=self.device_id)
        self.device_token, _ = issue_device_token_for_device(
            user=self.user.name,
            device_id=self.device_id,
            platform="android",
            wallet_id=self.wallet_id,
        )
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()

    def test_export_shape_wallet_scope_and_tombstones(self):
        in_wallet = f"acc-in-{frappe.generate_hash(length=6)}"
        deleted_wallet = f"acc-del-{frappe.generate_hash(length=6)}"
        out_wallet = f"wallet-other-{frappe.generate_hash(length=6)}"
        out_account = f"acc-out-{frappe.generate_hash(length=6)}"

        frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "name": in_wallet,
                "client_id": in_wallet,
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "account_name": "In Wallet",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
            }
        ).insert(ignore_permissions=True)

        deleted_doc = frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "name": deleted_wallet,
                "client_id": deleted_wallet,
                "wallet_id": self.wallet_id,
                "user": self.user.name,
                "account_name": "Deleted",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
                "is_deleted": 1,
            }
        ).insert(ignore_permissions=True)
        deleted_doc.deleted_at = frappe.utils.now_datetime()
        deleted_doc.save(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "Hisabi Wallet",
                "name": out_wallet,
                "client_id": out_wallet,
                "wallet_id": out_wallet,
                "wallet_name": "Other Wallet",
                "status": "active",
                "user": self.user.name,
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "Hisabi Wallet Member",
                "name": f"{out_wallet}:{self.user.name}",
                "client_id": f"{out_wallet}:{self.user.name}",
                "wallet_id": out_wallet,
                "wallet": out_wallet,
                "user": self.user.name,
                "role": "owner",
                "status": "active",
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "Hisabi Account",
                "name": out_account,
                "client_id": out_account,
                "wallet_id": out_wallet,
                "user": self.user.name,
                "account_name": "Out Wallet",
                "account_type": "cash",
                "currency": "SAR",
                "opening_balance": 0,
            }
        ).insert(ignore_permissions=True)

        payload = backup_export(wallet_id=self.wallet_id, format="hisabi_json_v1")
        self.assertEqual(payload.get("meta", {}).get("format"), "hisabi_json_v1")
        self.assertEqual(payload.get("meta", {}).get("wallet_id"), self.wallet_id)
        self.assertTrue(payload.get("meta", {}).get("exported_at"))
        self.assertTrue(payload.get("meta", {}).get("commit"))

        entities = payload.get("entities") or {}
        self.assertIn("Wallet", entities)
        self.assertIn("Account", entities)
        account_ids = {row.get("client_id") or row.get("name") for row in entities.get("Account", [])}
        self.assertIn(in_wallet, account_ids)
        self.assertIn(deleted_wallet, account_ids)
        self.assertNotIn(out_account, account_ids)

        deleted_row = next(row for row in entities.get("Account", []) if (row.get("client_id") or row.get("name")) == deleted_wallet)
        self.assertEqual(int(deleted_row.get("is_deleted") or 0), 1)
        self.assertTrue(deleted_row.get("deleted_at"))
