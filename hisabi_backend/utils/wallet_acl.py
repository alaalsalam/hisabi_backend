"""Wallet ACL helpers for shared wallet collaboration.

Server-authoritative: never trust client-provided ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import frappe
from frappe import _

from hisabi_backend.utils.audit_security import audit_security_event

WalletRole = Literal["owner", "admin", "member", "viewer"]

ROLE_RANK = {
    "viewer": 1,
    "member": 2,
    "admin": 3,
    "owner": 4,
}


@dataclass(frozen=True)
class WalletMemberInfo:
    wallet_id: str
    user: str
    role: WalletRole
    status: str


def _get_member_row(wallet_id: str, user: str) -> Optional[WalletMemberInfo]:
    row = frappe.db.get_value(
        "Hisabi Wallet Member",
        {"wallet": wallet_id, "user": user},
        ["wallet", "user", "role", "status"],
        as_dict=True,
    )
    if not row:
        return None
    return WalletMemberInfo(
        wallet_id=row.wallet,
        user=row.user,
        role=row.role,
        status=row.status,
    )


def require_wallet_member(wallet_id: str, user: str, min_role: WalletRole = "viewer") -> WalletMemberInfo:
    """Ensure user is an active member of wallet with sufficient role."""
    if not wallet_id:
        frappe.throw(_("wallet_id is required"), frappe.ValidationError)
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    if not frappe.db.exists("Hisabi Wallet", wallet_id):
        frappe.throw(_("Wallet not found"), frappe.ValidationError)

    member = _get_member_row(wallet_id, user)
    if not member or member.status != "active":
        audit_security_event("permission_denied", user=user, payload={"wallet_id": wallet_id, "reason": "not_member"})
        frappe.throw(_("Not a member of this wallet"), frappe.PermissionError)

    if ROLE_RANK.get(member.role, 0) < ROLE_RANK[min_role]:
        audit_security_event(
            "permission_denied",
            user=user,
            payload={"wallet_id": wallet_id, "reason": "insufficient_role", "role": member.role, "min_role": min_role},
        )
        frappe.throw(_("Insufficient wallet role"), frappe.PermissionError)

    return member


def get_wallets_for_user(user: str) -> list[dict]:
    """List wallets where user is an active member."""
    rows = frappe.db.sql(
        """
        SELECT m.wallet, m.role, m.status, w.wallet_name, w.status AS wallet_status
        FROM `tabHisabi Wallet Member` m
        JOIN `tabHisabi Wallet` w ON w.name = m.wallet
        WHERE m.user=%s AND m.status='active' AND w.is_deleted=0
        ORDER BY w.modified DESC
        """,
        (user,),
        as_dict=True,
    )
    return rows or []


def is_wallet_scoped(doctype: str) -> bool:
    """Return True if doctype is expected to have wallet_id field.

    Some auth-layer doctypes may not be wallet-scoped; treat them as not scoped.
    """
    meta = frappe.get_meta(doctype)
    return meta.has_field("wallet_id")
