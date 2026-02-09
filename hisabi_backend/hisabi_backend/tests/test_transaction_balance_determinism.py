import frappe
import json
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from hisabi_backend.api.v1.sync import sync_push
from hisabi_backend.domain.recalc_engine import compute_account_balance_from_ledger
from hisabi_backend.install import ensure_roles
from hisabi_backend.utils.security import issue_device_token_for_device
from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user


class TestTransactionBalanceDeterminism(FrappeTestCase):
    def setUp(self):
        ensure_roles()
        self.device_id = f"device-{frappe.generate_hash(length=6)}"
        email = f"tx_balance_{frappe.generate_hash(length=6)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Balance",
                "last_name": "Tester",
                "send_welcome_email": 0,
                "roles": [{"role": "Hisabi User"}],
            }
        ).insert(ignore_permissions=True)
        update_password(user.name, "test123A!")
        self.user = user.name
        frappe.set_user(self.user)

        self.wallet_id = ensure_default_wallet_for_user(self.user, device_id=self.device_id)
        token, _device = issue_device_token_for_device(
            user=self.user,
            device_id=self.device_id,
            platform="android",
            device_name="Pixel 8",
            wallet_id=self.wallet_id,
        )
        self.device_token = token
        frappe.local.request = type(
            "obj", (object,), {"headers": {"Authorization": f"Bearer {self.device_token}"}}
        )()

    def _push(self, items):
        response = sync_push(device_id=self.device_id, wallet_id=self.wallet_id, items=items)
        if hasattr(response, "get_data"):
            status = getattr(response, "status_code", 200)
            payload = json.loads(response.get_data(as_text=True) or "{}")
            if status != 200:
                raise AssertionError(f"sync_push http {status}: {payload}")
            response = payload.get("message", payload)
        for result in (response or {}).get("results", []):
            status = result.get("status")
            if status not in {"accepted", "duplicate"}:
                raise AssertionError(f"sync_push unexpected status: {response}")
        return response

    def _create_account(self, account_id: str, opening_balance: float) -> None:
        self._push(
            [
                {
                    "op_id": f"op-create-{account_id}",
                    "entity_type": "Hisabi Account",
                    "entity_id": account_id,
                    "operation": "create",
                    "payload": {
                        "client_id": account_id,
                        "account_name": account_id,
                        "account_type": "cash",
                        "currency": "SAR",
                        "opening_balance": opening_balance,
                    },
                }
            ]
        )

    def _create_expense_tx(self, tx_id: str, account_id: str, amount: float) -> None:
        self._push(
            [
                {
                    "op_id": f"op-create-{tx_id}",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": tx_id,
                    "operation": "create",
                    "payload": {
                        "client_id": tx_id,
                        "transaction_type": "expense",
                        "date_time": now_datetime().isoformat(),
                        "amount": amount,
                        "currency": "SAR",
                        "account": account_id,
                    },
                }
            ]
        )

    def test_create_expense_decreases_balance(self):
        self._create_account("acc-det-create", 100)
        self._create_expense_tx("tx-det-create", "acc-det-create", 30)

        account = frappe.get_doc("Hisabi Account", "acc-det-create")
        self.assertEqual(account.current_balance, 70)

    def test_update_transaction_amount_updates_balance(self):
        self._create_account("acc-det-update", 100)
        self._create_expense_tx("tx-det-update", "acc-det-update", 20)

        self._push(
            [
                {
                    "op_id": "op-update-tx-det-update",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-det-update",
                    "operation": "update",
                    "base_version": 1,
                    "payload": {
                        "client_id": "tx-det-update",
                        "amount": 35,
                    },
                }
            ]
        )

        account = frappe.get_doc("Hisabi Account", "acc-det-update")
        self.assertEqual(account.current_balance, 65)

    def test_delete_transaction_restores_balance(self):
        self._create_account("acc-det-delete", 100)
        self._create_expense_tx("tx-det-delete", "acc-det-delete", 25)

        self._push(
            [
                {
                    "op_id": "op-delete-tx-det-delete",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-det-delete",
                    "operation": "delete",
                    "base_version": 1,
                    "payload": {
                        "client_id": "tx-det-delete",
                    },
                }
            ]
        )

        account = frappe.get_doc("Hisabi Account", "acc-det-delete")
        self.assertEqual(account.current_balance, 100)

    def test_cross_account_move_updates_both_accounts(self):
        self._create_account("acc-det-a", 100)
        self._create_account("acc-det-b", 50)
        self._create_expense_tx("tx-det-move", "acc-det-a", 20)

        self._push(
            [
                {
                    "op_id": "op-update-tx-det-move-account",
                    "entity_type": "Hisabi Transaction",
                    "entity_id": "tx-det-move",
                    "operation": "update",
                    "base_version": 1,
                    "payload": {
                        "client_id": "tx-det-move",
                        "account": "acc-det-b",
                    },
                }
            ]
        )

        account_a = frappe.get_doc("Hisabi Account", "acc-det-a")
        account_b = frappe.get_doc("Hisabi Account", "acc-det-b")
        self.assertEqual(account_a.current_balance, 100)
        self.assertEqual(account_b.current_balance, 30)

    def test_compute_balance_from_ledger_helper_is_deterministic(self):
        opening = 100
        account_id = "acc-pure"
        ledger = [
            {"transaction_type": "income", "amount": 50, "account": account_id, "to_account": None},
            {"transaction_type": "expense", "amount": 20, "account": account_id, "to_account": None},
            {"transaction_type": "transfer", "amount": 10, "account": account_id, "to_account": "acc-other"},
            {"transaction_type": "transfer", "amount": 15, "account": "acc-other", "to_account": account_id},
        ]
        self.assertEqual(
            compute_account_balance_from_ledger(
                account_id=account_id,
                opening_balance=opening,
                ledger_entries=ledger,
            ),
            135,
        )
