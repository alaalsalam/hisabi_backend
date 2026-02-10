import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.backup import validate_restore
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestBackupRestoreValidate(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"backup_validate_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Backup",
                "last_name": "Validate",
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

    def test_validate_catches_wallet_mismatch_and_invalid_refs(self):
        payload = {
            "meta": {"format": "hisabi_json_v1", "wallet_id": self.wallet_id},
            "entities": {
                "Account": [
                    {
                        "client_id": "acc-v-1",
                        "wallet_id": self.wallet_id,
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                    }
                ],
                "Transaction": [
                    {
                        "client_id": "tx-v-1",
                        "wallet_id": self.wallet_id,
                        "transaction_type": "expense",
                        "amount": 5,
                        "currency": "SAR",
                        "account": "missing-acc",
                    }
                ],
                "Category": [
                    {
                        "client_id": "cat-v-1",
                        "wallet_id": "wallet-other",
                        "category_name": "Food",
                        "kind": "expense",
                    }
                ],
            },
        }

        frappe.local.response = {}
        res = validate_restore(wallet_id=self.wallet_id, payload=payload)
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)
        self.assertEqual((res.get("error") or {}).get("code"), "restore_validation_failed")

        details = (res.get("error") or {}).get("details") or []
        error_codes = {item.get("code") for item in details}
        self.assertIn("wallet_mismatch", error_codes)
        self.assertIn("invalid_reference", error_codes)
