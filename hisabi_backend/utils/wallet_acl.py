"""Wallet ACL helpers for shared wallet collaboration.

Server-authoritative: never trust client-provided ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Literal, Optional

import frappe
from frappe import _

from frappe.utils import now_datetime

from hisabi_backend.utils.audit_security import audit_security_event
from hisabi_backend.utils.sync_common import apply_common_sync_fields

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


def get_or_create_hisabi_user(user: str) -> frappe.model.document.Document:
    name = frappe.get_value("Hisabi User", {"user": user})
    if name:
        return frappe.get_doc("Hisabi User", name)
    doc = frappe.new_doc("Hisabi User")
    doc.user = user
    doc.save(ignore_permissions=True)
    return doc


def ensure_default_wallet_for_user(user: str, device_id: Optional[str] = None) -> str:
    """Ensure the user has a default wallet and return its id."""
    profile = get_or_create_hisabi_user(user)
    default_wallet = getattr(profile, "default_wallet", None)
    if default_wallet and frappe.db.exists("Hisabi Wallet", default_wallet):
        return default_wallet

    row = frappe.db.get_value(
        "Hisabi Wallet Member",
        {"user": user, "status": "active"},
        ["wallet"],
        order_by="modified desc",
        as_dict=True,
    )
    if row and row.wallet:
        profile.default_wallet = row.wallet
        profile.save(ignore_permissions=True)
        return row.wallet

    # Fall back to any non-removed membership before creating a new wallet.
    row = frappe.db.get_value(
        "Hisabi Wallet Member",
        {"user": user, "status": ["!=", "removed"]},
        ["wallet"],
        order_by="modified desc",
        as_dict=True,
    )
    if row and row.wallet:
        profile.default_wallet = row.wallet
        profile.save(ignore_permissions=True)
        return row.wallet

    wallet_id = f"wallet-u-{frappe.generate_hash(user, length=12)}"
    if frappe.db.exists("Hisabi Wallet", wallet_id):
        wallet_id = f"{wallet_id}-{secrets.token_hex(2)}"

    wallet = frappe.new_doc("Hisabi Wallet")
    wallet.client_id = wallet_id
    wallet.name = wallet_id
    wallet.wallet_name = "My Wallet"
    wallet.status = "active"
    wallet.owner_user = user
    wallet.created_from_device = device_id
    apply_common_sync_fields(wallet, bump_version=True, mark_deleted=False)
    wallet.save(ignore_permissions=True)

    member = frappe.new_doc("Hisabi Wallet Member")
    member.wallet = wallet_id
    member.user = user
    member.role = "owner"
    member.status = "active"
    member.joined_at = now_datetime()
    apply_common_sync_fields(member, bump_version=True, mark_deleted=False)
    member.save(ignore_permissions=True)

    profile.default_wallet = wallet_id
    profile.save(ignore_permissions=True)
    return wallet_id


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


def get_wallets_for_user(user: str, default_wallet_id: Optional[str] = None) -> list[dict]:
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
    if not rows:
        return []
    for row in rows:
        if default_wallet_id:
            row["isDefault"] = row.get("wallet") == default_wallet_id
    return rows


def is_wallet_scoped(doctype: str) -> bool:
    """Return True if doctype is expected to have wallet_id field.

    Some auth-layer doctypes may not be wallet-scoped; treat them as not scoped.
    """
    meta = frappe.get_meta(doctype)
    return meta.has_field("wallet_id")
