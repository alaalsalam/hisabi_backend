"""On-demand data-integrity endpoints (verify + safe heal) for a wallet.

Thin API layer over hisabi_backend.domain.integrity. Every function starts with device-token auth
and a wallet-membership check before touching wallet data.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe import _

from hisabi_backend.domain import integrity as integrity_domain
from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member


def _require_wallet_scope(wallet_id: Optional[str]) -> str:
    # In the domain layer a None wallet_id means THE ENTIRE FLEET (scheduler semantics). That must
    # never be reachable from the HTTP surface — an authenticated user must always name a wallet
    # they belong to, or they'd receive other users' balances (verify) / mutate fleet-wide (heal).
    if not wallet_id:
        frappe.throw(_("wallet_id is required"), frappe.ValidationError)
    return validate_client_id(wallet_id)


def verify(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: report any aggregate that drifted from its ledger-derived value."""
    user, _device = require_device_token_auth()
    wallet_id = _require_wallet_scope(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")
    return integrity_domain.verify_wallet(wallet_id)


def heal(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
    """Auto-correct the safe pure derivations (budgets, debts). Accounts/goals are report-only."""
    user, _device = require_device_token_auth()
    wallet_id = _require_wallet_scope(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")
    return integrity_domain.heal_wallet(wallet_id)


def reconcile_account(
    wallet_id: Optional[str] = None,
    account_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """User-confirmed fix: adopt an account's unexplained balance as its opening balance."""
    user, _device = require_device_token_auth()
    wallet_id = _require_wallet_scope(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")
    if not account_id:
        frappe.throw(_("account_id is required"), frappe.ValidationError)
    account_id = validate_client_id(account_id)
    # Resolve within the wallet so a member cannot reconcile another wallet's account.
    name = frappe.db.get_value(
        "Hisabi Account", {"client_id": account_id, "wallet_id": wallet_id, "is_deleted": 0}, "name"
    )
    if not name:
        frappe.throw(_("Account not found in wallet"), frappe.ValidationError)
    return integrity_domain.reconcile_account_opening(name)
