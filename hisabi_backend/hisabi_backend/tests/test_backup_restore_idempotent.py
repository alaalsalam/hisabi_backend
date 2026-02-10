import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.backup import apply_restore
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBackupRestoreIdempotent(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"backup_apply_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Backup",
                "last_name": "Apply",
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

    def test_apply_twice_is_idempotent(self):
        account_id = f"acc-r-{frappe.generate_hash(length=6)}"
        category_id = f"cat-r-{frappe.generate_hash(length=6)}"
        tx_id = f"tx-r-{frappe.generate_hash(length=6)}"

        payload = {
            "meta": {"format": "hisabi_json_v1", "wallet_id": self.wallet_id},
            "entities": {
                "Wallet": [
                    {
                        "client_id": self.wallet_id,
                        "wallet_id": self.wallet_id,
                        "wallet_name": "Restore Wallet",
                        "status": "active",
                    }
                ],
                "Account": [
                    {
                        "client_id": account_id,
                        "wallet_id": self.wallet_id,
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 10,
                    }
                ],
                "Category": [
                    {
                        "client_id": category_id,
                        "wallet_id": self.wallet_id,
                        "category_name": "Food",
                        "kind": "expense",
                    }
                ],
                "Transaction": [
                    {
                        "client_id": tx_id,
                        "wallet_id": self.wallet_id,
                        "transaction_type": "expense",
                        "date_time": "2026-02-01 10:00:00",
                        "amount": 3,
                        "currency": "SAR",
                        "account": account_id,
                        "category": category_id,
                    }
                ],
            },
        }

        first = apply_restore(wallet_id=self.wallet_id, payload=payload, mode="merge")
        self.assertEqual(first.get("status"), "ok")

        second = apply_restore(wallet_id=self.wallet_id, payload=payload, mode="merge")
        self.assertEqual(second.get("status"), "ok")

        account_rows = frappe.get_all("Hisabi Account", filters={"wallet_id": self.wallet_id, "client_id": account_id}, fields=["name"])
        category_rows = frappe.get_all(
            "Hisabi Category", filters={"wallet_id": self.wallet_id, "client_id": category_id}, fields=["name"]
        )
        tx_rows = frappe.get_all("Hisabi Transaction", filters={"wallet_id": self.wallet_id, "client_id": tx_id}, fields=["name"])

        self.assertEqual(len(account_rows), 1)
        self.assertEqual(len(category_rows), 1)
        self.assertEqual(len(tx_rows), 1)
