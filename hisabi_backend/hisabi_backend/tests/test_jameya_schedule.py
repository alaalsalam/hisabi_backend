import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


class TestJameyaSchedule(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"jameya_test_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Jameya",
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

    def test_schedule_created(self):
        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-jameya-1",
                    "entity_type": "Hisabi Jameya",
                    "entity_id": "jam-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "jam-1",
                        "jameya_name": "Friends",
                        "currency": "SAR",
                        "monthly_amount": 200,
                        "total_members": 5,
                        "my_turn": 2,
                        "period": "monthly",
                        "start_date": now_datetime(),
                    },
                }
            ],
        )

        count = frappe.db.count("Hisabi Jameya Payment", {"jameya": "jam-1"})
        self.assertEqual(count, 5)
