import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.auth import register_device
from hisabi_backend.api.v1 import wallet_create
from hisabi_backend.api.v1.reports_finance import cashflow, category_breakdown, report_budgets
from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.install import ensure_roles


class TestBudgetsReports(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        email = f"budget_test_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "Budget",
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

    def test_budget_spent_report(self):
        start = now_datetime()
        end = add_months(start, 1)

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-b1",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-b1",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 0,
                    },
                },
                {
                    "op_id": "op-cat-1",
                    "entity_type": "Hisabi Category",
                    "entity_id": "cat-b1",
                    "operation": "create",
                    "payload": {
                        "client_id": "cat-b1",
                        "category_name": "Food",
                        "kind": "expense",
                        "color": "#000",
                    },
                },
                {
                    "op_id": "op-budget-1",
                    "entity_type": "Hisabi Budget",
                    "entity_id": "budget-1",
                    "operation": "create",
                    "payload": {
                        "client_id": "budget-1",
                        "budget_name": "Food Budget",
                        "period": "monthly",
                        "scope_type": "category",
                        "category": "cat-b1",
                        "currency": "SAR",
                        "amount": 500,
                        "start_date": start,
                        "end_date": end,
                    },
                },
            ],
        )

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-tx-1",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-b1",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-b1",
                        "transaction_type": "expense",
                        "date_time": now_datetime(),
                        "amount": 120,
                        "currency": "SAR",
                        "account": "acc-b1",
                        "category": "cat-b1",
                    },
                }
            ],
        )

        response = report_budgets(from_date=start.isoformat(), to_date=end.isoformat(), wallet_id=self.wallet_id, device_id=self.device_id)
        budgets = response.get("budgets")
        self.assertTrue(budgets)
        self.assertEqual(budgets[0]["spent_amount"], 120)

    def test_category_breakdown_and_cashflow_reports(self):
        start = now_datetime()
        end = add_months(start, 1)

        sync_push(
            device_id=self.device_id,
            wallet_id=self.wallet_id,
            items=[
                {
                    "op_id": "op-acc-r1",
                    "entity_type": "Hisabi Account",
                    "entity_id": "acc-r1",
                    "operation": "create",
                    "payload": {
                        "client_id": "acc-r1",
                        "account_name": "Cash",
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": 0,
                    },
                },
                {
                    "op_id": "op-cat-r1",
                    "entity_type": "Hisabi Category",
                    "entity_id": "cat-r1",
                    "operation": "create",
                    "payload": {
                        "client_id": "cat-r1",
                        "category_name": "Food",
                        "kind": "expense",
                    },
                },
                {
                    "op_id": "op-tx-r1-expense",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-r1-expense",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-r1-expense",
                        "transaction_type": "expense",
                        "date_time": start,
                        "amount": 40,
                        "currency": "SAR",
                        "account": "acc-r1",
                        "category": "cat-r1",
                    },
                },
                {
                    "op_id": "op-tx-r1-income",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-r1-income",
                    "operation": "create",
                    "payload": {
                        "client_id": "tx-r1-income",
                        "transaction_type": "income",
                        "date_time": start,
                        "amount": 100,
                        "currency": "SAR",
                        "account": "acc-r1",
                    },
                },
            ],
        )

        breakdown = category_breakdown(
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )
        categories = breakdown.get("categories") or []
        self.assertTrue(categories)
        self.assertTrue(any(row.get("category_id") == "cat-r1" for row in categories))
        totals = breakdown.get("totals") or {}
        self.assertEqual(totals.get("income"), 100)
        self.assertEqual(totals.get("expense"), 40)

        flow = cashflow(
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            wallet_id=self.wallet_id,
            device_id=self.device_id,
        )
        points = flow.get("points") or []
        self.assertTrue(points)
        flow_totals = flow.get("totals") or {}
        self.assertEqual(flow_totals.get("income"), 100)
        self.assertEqual(flow_totals.get("expense"), 40)

    def test_reports_require_wallet_id(self):
        breakdown = category_breakdown(device_id=self.device_id)
        self.assertEqual(getattr(breakdown, "status_code", None), 422)
        flow = cashflow(device_id=self.device_id)
        self.assertEqual(getattr(flow, "status_code", None), 422)
