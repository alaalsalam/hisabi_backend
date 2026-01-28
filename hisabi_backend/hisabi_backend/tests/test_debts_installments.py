import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


class TestDebtsInstallments(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"debt_test_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Debt",
            "last_name": "Tester",
            "send_welcome_email": 0,
            "roles": [{"role": "Hisabi User"}],
        }).insert(ignore_permissions=True)
        update_password(user.name, "test123")
        frappe.set_user(user.name)
        self.user = user
        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        device = register_device(self.device_id, "android", "Pixel 8")
        self.device_token = device.get("device_token")
        frappe.local.request = type("obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}})()
        self.wallet_id = f"wallet-{frappe.generate_hash(length=6)}"
        wallet_create(client_id=self.wallet_id, wallet_name="Test Wallet", device_id=self.device_id)

    def test_debt_remaining_updates(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-debt-2",
                    "entity_type": "Hisabi Debt",
                    "entity_id": "debt-2",
                    "operation": "create",
                    "payload": {
                        "client_id": "debt-2",
                        "debt_name": "Loan",
                        "direction": "owe",
                        "currency": "SAR",
                        "principal_amount": 300,
                        "remaining_amount": 300,
                    },
                },
                {
                    "op_id": "op-installment-2",
                    "entity_type": "Hisabi Debt Installment",
                    "entity_id": "inst-2",
                    "operation": "create",
                    "payload": {
                        "client_id": "inst-2",
                        "debt": "debt-2",
                        "amount": 120,
                        "status": "paid",
                        "paid_amount": 120,
                    },
                },
            ],
        )

        debt = frappe.get_doc("Hisabi Debt", "debt-2")
        self.assertEqual(debt.remaining_amount, 180)
