"""User lifecycle operations: freeze/unfreeze and full account deletion."""

from __future__ import annotations

from typing import Dict, List, Set

import frappe
from frappe import _
from frappe.utils import now_datetime

PROTECTED_USERS: Set[str] = {"Administrator", "Guest"}


def _normalize_user(value: str | None) -> str:
    return (value or "").strip()


def _ensure_user_is_deletable(user: str) -> None:
    if user in PROTECTED_USERS:
        frappe.throw(_("Protected system users cannot be modified"), frappe.PermissionError)
    if not frappe.db.exists("User", user):
        frappe.throw(_("User not found"), frappe.DoesNotExistError)


def _count_rows(doctype: str, filters: dict) -> int:
    try:
        return int(frappe.db.count(doctype, filters))
    except Exception:
        return 0


def _delete_rows(doctype: str, filters: dict) -> int:
    deleted = _count_rows(doctype, filters)
    if deleted > 0:
        frappe.db.delete(doctype, filters)
    return deleted


def get_owned_wallet_ids(user: str) -> List[str]:
    user = _normalize_user(user)
    if not user:
        return []
    if not frappe.db.exists("DocType", "Hisabi Wallet"):
        return []
    return frappe.get_all("Hisabi Wallet", filters={"owner_user": user}, pluck="name", limit_page_length=0)


def is_user_frozen(user: str) -> bool:
    user = _normalize_user(user)
    if not user:
        return True
    if user in PROTECTED_USERS:
        return False
    enabled = frappe.db.get_value("User", user, "enabled")
    if enabled is not None and int(enabled or 0) == 0:
        return True
    hisabi_name = frappe.db.get_value("Hisabi User", {"user": user})
    if not hisabi_name:
        return False
    status = (frappe.db.get_value("Hisabi User", hisabi_name, "account_status") or "").strip().lower()
    return status == "frozen"


def set_user_frozen_state(user: str, *, freeze: bool, actor: str | None = None, reason: str | None = None) -> Dict[str, object]:
    user = _normalize_user(user)
    actor = _normalize_user(actor) or frappe.session.user
    _ensure_user_is_deletable(user)

    profile_name = frappe.db.get_value("Hisabi User", {"user": user})
    if profile_name:
        profile = frappe.get_doc("Hisabi User", profile_name)
    else:
        profile = frappe.new_doc("Hisabi User")
        profile.user = user

    profile.account_status = "Frozen" if freeze else "Active"
    if freeze:
        profile.frozen_at = now_datetime()
        profile.frozen_by = actor
        profile.freeze_reason = (reason or "").strip() or None
    else:
        profile.frozen_at = None
        profile.frozen_by = None
        profile.freeze_reason = None
    profile.save(ignore_permissions=True)

    frappe.db.set_value("User", user, "enabled", 0 if freeze else 1, update_modified=False)

    revoked_devices = 0
    if freeze and frappe.db.exists("DocType", "Hisabi Device"):
        revoked_devices = _count_rows("Hisabi Device", {"user": user, "status": "active"})
        if revoked_devices:
            frappe.db.sql(
                """
                UPDATE `tabHisabi Device`
                SET status='revoked', token_hash=NULL, token_last4=NULL, device_token_hash=NULL, modified=NOW()
                WHERE user=%s AND status='active'
                """,
                (user,),
            )

    return {
        "user": user,
        "status": profile.account_status,
        "revoked_devices": revoked_devices,
    }


def delete_user_account_and_data(
    user: str,
    *,
    actor: str | None = None,
    delete_frappe_user: bool = True,
) -> Dict[str, object]:
    user = _normalize_user(user)
    actor = _normalize_user(actor) or frappe.session.user
    _ensure_user_is_deletable(user)
    if actor == user:
        frappe.throw(_("Self-delete is not allowed from this screen"), frappe.PermissionError)

    owned_wallet_ids = get_owned_wallet_ids(user)
    member_wallet_ids = []
    if frappe.db.exists("DocType", "Hisabi Wallet Member"):
        member_wallet_ids = frappe.get_all(
            "Hisabi Wallet Member",
            filters={"user": user},
            pluck="wallet",
            limit_page_length=0,
        )

    deleted_counts: Dict[str, int] = {}
    wallet_scoped_doctypes = [
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
        "Hisabi Transaction Allocation",
        "Hisabi Transaction Bucket",
        "Hisabi Transaction Bucket Expense",
        "Hisabi Recurring Rule",
        "Hisabi Recurring Instance",
        "Hisabi Jameya",
        "Hisabi Jameya Payment",
        "Hisabi Attachment",
        "Hisabi FX Rate",
        "Hisabi Custom Currency",
        "Hisabi Settings",
        "Hisabi Audit Log",
        "Hisabi Device",
        "Hisabi Wallet Invite",
    ]

    if owned_wallet_ids:
        for doctype in wallet_scoped_doctypes:
            if not frappe.db.exists("DocType", doctype):
                continue
            meta = frappe.get_meta(doctype)
            if meta.has_field("wallet_id"):
                count = _delete_rows(doctype, {"wallet_id": ["in", owned_wallet_ids]})
                if count:
                    deleted_counts[doctype] = deleted_counts.get(doctype, 0) + count
            if meta.has_field("wallet"):
                count = _delete_rows(doctype, {"wallet": ["in", owned_wallet_ids]})
                if count:
                    deleted_counts[doctype] = deleted_counts.get(doctype, 0) + count

        count = _delete_rows("Hisabi Wallet Member", {"wallet": ["in", owned_wallet_ids]})
        if count:
            deleted_counts["Hisabi Wallet Member"] = deleted_counts.get("Hisabi Wallet Member", 0) + count
        count = _delete_rows("Hisabi Wallet", {"name": ["in", owned_wallet_ids]})
        if count:
            deleted_counts["Hisabi Wallet"] = deleted_counts.get("Hisabi Wallet", 0) + count

    # Remove user membership/invites from shared wallets too.
    if member_wallet_ids:
        count = _delete_rows("Hisabi Wallet Member", {"user": user})
        if count:
            deleted_counts["Hisabi Wallet Member"] = deleted_counts.get("Hisabi Wallet Member", 0) + count

    if frappe.db.exists("DocType", "Hisabi Wallet Invite"):
        for fieldname in ("invited_by", "accepted_by"):
            count = _delete_rows("Hisabi Wallet Invite", {fieldname: user})
            if count:
                deleted_counts["Hisabi Wallet Invite"] = deleted_counts.get("Hisabi Wallet Invite", 0) + count

    # Generic user-linked cleanup for Hisabi module doctypes.
    hisabi_doctypes = frappe.get_all(
        "DocType",
        filters={"module": "Hisabi Backend", "issingle": 0},
        pluck="name",
        limit_page_length=0,
    )
    skip = {"Hisabi Wallet", "Hisabi Wallet Member", "Hisabi Wallet Invite", "Hisabi User"}
    for doctype in hisabi_doctypes:
        if doctype in skip or not frappe.db.exists("DocType", doctype):
            continue
        meta = frappe.get_meta(doctype)
        user_field = meta.get_field("user")
        if user_field and (user_field.options or "") == "User":
            count = _delete_rows(doctype, {"user": user})
            if count:
                deleted_counts[doctype] = deleted_counts.get(doctype, 0) + count

    if frappe.db.exists("DocType", "Hisabi User"):
        count = _delete_rows("Hisabi User", {"user": user})
        if count:
            deleted_counts["Hisabi User"] = deleted_counts.get("Hisabi User", 0) + count

    if delete_frappe_user:
        # force=True ensures linked docs already removed do not block final account delete.
        frappe.delete_doc("User", user, ignore_permissions=True, force=True, delete_permanently=True)
        deleted_counts["User"] = 1
    else:
        frappe.db.set_value("User", user, "enabled", 0, update_modified=False)

    return {
        "user": user,
        "deleted_by": actor,
        "owned_wallets": owned_wallet_ids,
        "member_wallets": member_wallet_ids,
        "deleted_counts": deleted_counts,
    }

