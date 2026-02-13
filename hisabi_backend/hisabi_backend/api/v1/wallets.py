"""Wallet collaboration APIs (v1)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import normalize_phone, validate_client_id
from hisabi_backend.utils.wallet_acl import (
    get_or_create_hisabi_user,
    ensure_default_wallet_for_user,
    get_wallets_for_user,
    require_wallet_member,
)
from hisabi_backend.utils.audit_security import audit_security_event


def _generate_invite_code() -> str:
    return frappe.generate_hash(length=10).upper().replace("-", "")[:10]


def _generate_invite_token() -> str:
    return frappe.generate_hash(length=32)


WALLET_DELETE_CASCADE_DOCTYPES: Tuple[str, ...] = (
    "Hisabi Wallet Member",
    "Hisabi Account",
    "Hisabi Category",
    "Hisabi Transaction",
    "Hisabi Debt",
    "Hisabi Debt Installment",
    "Hisabi Debt Request",
    "Hisabi Budget",
    "Hisabi Goal",
    "Hisabi Bucket",
    "Hisabi Bucket Template",
    "Hisabi Allocation Rule",
    "Hisabi Allocation Rule Line",
    "Hisabi Transaction Bucket",
    "Hisabi Transaction Bucket Expense",
    "Hisabi Recurring Rule",
    "Hisabi Recurring Instance",
    "Hisabi Transaction Allocation",
    "Hisabi Jameya",
    "Hisabi Jameya Payment",
    "Hisabi Attachment",
    "Hisabi FX Rate",
    "Hisabi Custom Currency",
    "Hisabi User Settings",
)


def _wallet_filter_for_doctype(doctype: str, wallet_id: str) -> Optional[Dict[str, Any]]:
    if not frappe.db.exists("DocType", doctype):
        return None
    meta = frappe.get_meta(doctype)
    if meta.has_field("wallet_id"):
        return {"wallet_id": wallet_id}
    if meta.has_field("wallet"):
        return {"wallet": wallet_id}
    return None


def _count_doctype_rows(doctype: str, wallet_id: str, *, active_only: bool = False) -> int:
    filters = _wallet_filter_for_doctype(doctype, wallet_id)
    if filters is None:
        return 0
    if active_only and frappe.get_meta(doctype).has_field("is_deleted"):
        filters["is_deleted"] = 0
    return cint(frappe.db.count(doctype, filters))


def _collect_wallet_delete_counts(wallet_id: str) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for doctype in WALLET_DELETE_CASCADE_DOCTYPES:
        count = _count_doctype_rows(doctype, wallet_id, active_only=True)
        if count > 0:
            counts[doctype] = count
    transaction_count = counts.get("Hisabi Transaction", 0)
    active_members = cint(
        frappe.db.count("Hisabi Wallet Member", {"wallet": wallet_id, "status": "active", "is_deleted": 0})
    )
    return {
        "wallet_id": wallet_id,
        "transaction_count": transaction_count,
        "active_member_count": active_members,
        "counts_by_doctype": counts,
    }


def _soft_delete_wallet_scope(wallet_id: str) -> Dict[str, int]:
    deleted_counts: Dict[str, int] = {}
    for doctype in WALLET_DELETE_CASCADE_DOCTYPES:
        filters = _wallet_filter_for_doctype(doctype, wallet_id)
        if filters is None:
            continue
        names = frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=5000)
        if not names:
            continue
        deleted_in_doctype = 0
        for name in names:
            doc = frappe.get_doc(doctype, name)
            if doctype == "Hisabi Wallet Member":
                doc.status = "removed"
                if doc.meta.has_field("removed_at") and not doc.removed_at:
                    doc.removed_at = now_datetime()
            apply_common_sync_fields(doc, bump_version=True, mark_deleted=True)
            doc.save(ignore_permissions=True)
            deleted_in_doctype += 1
        if deleted_in_doctype > 0:
            deleted_counts[doctype] = deleted_in_doctype

    wallet = frappe.get_doc("Hisabi Wallet", wallet_id)
    wallet.status = "archived"
    apply_common_sync_fields(wallet, bump_version=True, mark_deleted=True)
    wallet.save(ignore_permissions=True)
    deleted_counts["Hisabi Wallet"] = 1
    return deleted_counts


@frappe.whitelist(allow_guest=False)
def wallets_list(device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    default_wallet_id = ensure_default_wallet_for_user(user, device_id=device_id)
    return {
        "wallets": get_wallets_for_user(user, default_wallet_id=default_wallet_id),
        "default_wallet_id": default_wallet_id,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def list_wallets(device_id: Optional[str] = None) -> Dict[str, Any]:
    payload = wallets_list(device_id=device_id)
    wallets = payload.get("wallets") or []
    wallet_ids = [row.get("wallet") for row in wallets if isinstance(row, dict) and row.get("wallet")]
    return {
        "wallet_ids": wallet_ids,
        "default_wallet_id": payload.get("default_wallet_id"),
        "server_time": payload.get("server_time") or now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_create(client_id: str, wallet_name: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, device = require_device_token_auth()
    client_id = validate_client_id(client_id)
    wallet_name = (wallet_name or "").strip()
    if not wallet_name:
        frappe.throw(_("wallet_name is required"), frappe.ValidationError)

    if frappe.db.exists("Hisabi Wallet", client_id):
        wallet = frappe.get_doc("Hisabi Wallet", client_id)
    else:
        wallet = frappe.new_doc("Hisabi Wallet")
        wallet.client_id = client_id
        wallet.wallet_name = wallet_name
        wallet.status = "active"
        wallet.owner_user = user
        if device:
            wallet.created_from_device = device.device_id
        apply_common_sync_fields(wallet, bump_version=True, mark_deleted=False)
        wallet.save(ignore_permissions=True)

    if not frappe.db.exists("Hisabi Wallet Member", {"wallet": wallet.name, "user": user}):
        member = frappe.new_doc("Hisabi Wallet Member")
        member.wallet = wallet.name
        member.user = user
        member.role = "owner"
        member.status = "active"
        member.joined_at = now_datetime()
        apply_common_sync_fields(member, bump_version=True, mark_deleted=False)
        member.save(ignore_permissions=True)

    return {
        "wallet": wallet.as_dict(),
        "member": frappe.get_value(
            "Hisabi Wallet Member", {"wallet": wallet.name, "user": user}, ["role", "status"], as_dict=True
        ),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_update(wallet_id: str, wallet_name: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    wallet_name = (wallet_name or "").strip()
    if not wallet_name:
        frappe.throw(_("wallet_name is required"), frappe.ValidationError)

    member = require_wallet_member(wallet_id, user, min_role="admin")
    if member.status != "active":
        frappe.throw(_("Not a member of this wallet"), frappe.PermissionError)

    wallet = frappe.get_doc("Hisabi Wallet", wallet_id)
    if cint(getattr(wallet, "is_deleted", 0)) == 1:
        frappe.throw(_("Wallet already deleted"), frappe.ValidationError)

    wallet.wallet_name = wallet_name
    apply_common_sync_fields(wallet, bump_version=True, mark_deleted=False)
    wallet.save(ignore_permissions=True)
    audit_security_event("wallet_updated", user=user, payload={"wallet_id": wallet_id, "wallet_name": wallet_name})

    return {
        "wallet": wallet.as_dict(),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_delete_preview(wallet_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    member = require_wallet_member(wallet_id, user, min_role="owner")
    if member.role != "owner":
        frappe.throw(_("Only owner can delete wallet"), frappe.PermissionError)
    if not frappe.db.exists("Hisabi Wallet", wallet_id):
        frappe.throw(_("Wallet not found"), frappe.ValidationError)
    return {
        **_collect_wallet_delete_counts(wallet_id),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_delete(
    wallet_id: str,
    confirm_delete_transactions: Optional[int] = 0,
    expected_transaction_count: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    member = require_wallet_member(wallet_id, user, min_role="owner")
    if member.role != "owner":
        frappe.throw(_("Only owner can delete wallet"), frappe.PermissionError)

    wallet = frappe.get_doc("Hisabi Wallet", wallet_id)
    if wallet.owner_user != user:
        frappe.throw(_("Only wallet owner can delete this wallet"), frappe.PermissionError)

    summary = _collect_wallet_delete_counts(wallet_id)
    tx_count = cint(summary.get("transaction_count") or 0)
    if tx_count > 0 and not cint(confirm_delete_transactions):
        frappe.throw(_("confirm_delete_transactions is required"), frappe.ValidationError)
    if expected_transaction_count is not None and cint(expected_transaction_count) != tx_count:
        frappe.throw(_("transaction_count_changed"), frappe.ValidationError)

    active_others = cint(
        frappe.db.count(
            "Hisabi Wallet Member",
            {"wallet": wallet_id, "status": "active", "is_deleted": 0, "user": ["!=", user]},
        )
    )
    if active_others > 0:
        frappe.throw(_("Remove active members before deleting wallet"), frappe.PermissionError)

    deleted_counts = _soft_delete_wallet_scope(wallet_id)

    profile = get_or_create_hisabi_user(user)
    if getattr(profile, "default_wallet", None) == wallet_id:
        profile.default_wallet = None
        profile.save(ignore_permissions=True)
    next_default_wallet_id = ensure_default_wallet_for_user(user, device_id=device_id)

    audit_security_event(
        "wallet_deleted",
        user=user,
        payload={
            "wallet_id": wallet_id,
            "transaction_count": tx_count,
            "deleted_counts": deleted_counts,
            "next_default_wallet_id": next_default_wallet_id,
        },
    )

    return {
        "status": "deleted",
        "wallet_id": wallet_id,
        "transaction_count": tx_count,
        "deleted_counts": deleted_counts,
        "next_default_wallet_id": next_default_wallet_id,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_invite_create(
    wallet_id: str,
    role_to_grant: str = "member",
    target_phone: Optional[str] = None,
    target_email: Optional[str] = None,
    expires_in_hours: int = 72,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="admin")

    role_to_grant = (role_to_grant or "member").strip().lower()
    if role_to_grant not in {"admin", "member", "viewer"}:
        frappe.throw(_("Invalid role_to_grant"), frappe.ValidationError)

    if target_phone:
        target_phone = normalize_phone(target_phone)
    if target_email:
        target_email = target_email.strip().lower()

    expires_at = add_to_date(now_datetime(), hours=int(expires_in_hours or 72))

    invite = frappe.new_doc("Hisabi Wallet Invite")
    invite.client_id = f"invite-{frappe.generate_hash(length=16)}"
    invite.wallet = wallet_id
    invite.invited_by = user
    invite.invite_code = _generate_invite_code()
    invite.invite_link_token = _generate_invite_token()
    invite.target_phone = target_phone
    invite.target_email = target_email
    invite.role_to_grant = role_to_grant
    invite.status = "active"
    invite.expires_at = expires_at
    invite.save(ignore_permissions=True)
    audit_security_event("wallet_invite_created", user=user, payload={"wallet_id": wallet_id, "role_to_grant": role_to_grant})

    return {
        "invite": {
            "wallet_id": wallet_id,
            "invite_code": invite.invite_code,
            "token": invite.invite_link_token,
            "role_to_grant": invite.role_to_grant,
            "expires_at": invite.expires_at,
        },
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_invite_accept(
    invite_code: Optional[str] = None, token: Optional[str] = None, device_id: Optional[str] = None
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    filters = {"status": "active"}
    if invite_code:
        filters["invite_code"] = invite_code.strip().upper()
    elif token:
        filters["invite_link_token"] = token.strip()
    else:
        frappe.throw(_("invite_code or token is required"), frappe.ValidationError)

    invite_name = frappe.get_value("Hisabi Wallet Invite", filters)
    if not invite_name:
        frappe.throw(_("Invalid invite"), frappe.ValidationError)

    invite = frappe.get_doc("Hisabi Wallet Invite", invite_name)
    if invite.expires_at and invite.expires_at < now_datetime():
        invite.status = "expired"
        invite.save(ignore_permissions=True)
        frappe.throw(_("Invite expired"), frappe.ValidationError)

    wallet_id = invite.wallet

    # Create or reactivate membership
    member_name = frappe.get_value("Hisabi Wallet Member", {"wallet": wallet_id, "user": user})
    if member_name:
        member = frappe.get_doc("Hisabi Wallet Member", member_name)
        member.role = invite.role_to_grant
        member.status = "active"
        member.joined_at = member.joined_at or now_datetime()
        member.removed_at = None
        apply_common_sync_fields(member, bump_version=True, mark_deleted=False)
        member.save(ignore_permissions=True)
    else:
        member = frappe.new_doc("Hisabi Wallet Member")
        member.wallet = wallet_id
        member.user = user
        member.role = invite.role_to_grant
        member.status = "active"
        member.joined_at = now_datetime()
        apply_common_sync_fields(member, bump_version=True, mark_deleted=False)
        member.save(ignore_permissions=True)

    invite.status = "accepted"
    invite.accepted_by = user
    invite.accepted_at = now_datetime()
    invite.save(ignore_permissions=True)
    audit_security_event("wallet_invite_accepted", user=user, payload={"wallet_id": wallet_id, "role": member.role})

    return {
        "wallet_id": wallet_id,
        "role": member.role,
        "status": member.status,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def wallet_member_remove(wallet_id: str, user_to_remove: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="admin")

    member_name = frappe.get_value("Hisabi Wallet Member", {"wallet": wallet_id, "user": user_to_remove})
    if not member_name:
        frappe.throw(_("Member not found"), frappe.ValidationError)
    member = frappe.get_doc("Hisabi Wallet Member", member_name)
    if member.role == "owner":
        frappe.throw(_("Cannot remove owner"), frappe.PermissionError)
    if user_to_remove == user:
        frappe.throw(_("Cannot remove self; use wallet_leave"), frappe.PermissionError)

    member.status = "removed"
    member.removed_at = now_datetime()
    apply_common_sync_fields(member, bump_version=True, mark_deleted=False)
    member.save(ignore_permissions=True)
    return {"status": "removed", "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def wallet_leave(wallet_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    member = require_wallet_member(wallet_id, user, min_role="viewer")
    if member.role == "owner":
        frappe.throw(_("Owner cannot leave wallet"), frappe.PermissionError)

    member_name = frappe.get_value("Hisabi Wallet Member", {"wallet": wallet_id, "user": user})
    m = frappe.get_doc("Hisabi Wallet Member", member_name)
    m.status = "removed"
    m.removed_at = now_datetime()
    apply_common_sync_fields(m, bump_version=True, mark_deleted=False)
    m.save(ignore_permissions=True)
    return {"status": "left", "server_time": now_datetime().isoformat()}
