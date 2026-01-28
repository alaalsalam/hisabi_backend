from __future__ import annotations

import hashlib

import frappe
from frappe.utils import now_datetime

from hisabi_backend.utils.sync_common import apply_common_sync_fields


def _default_wallet_id_for_user(user: str) -> str:
    h = hashlib.sha256(user.encode("utf-8")).hexdigest()[:12]
    return f"wallet-u-{h}"


def execute() -> None:
    """Backfill wallet_id for existing user-scoped Hisabi docs into a default per-user wallet.

    This prevents breaking existing installs when wallet_id becomes required for shared-wallet ACL.
    """
    if not frappe.db.exists("DocType", "Hisabi Wallet") or not frappe.db.exists("DocType", "Hisabi Wallet Member"):
        return

    doctypes = [
        "Hisabi Account",
        "Hisabi Category",
        "Hisabi Transaction",
        "Hisabi Bucket",
        "Hisabi Allocation Rule",
        "Hisabi Allocation Rule Line",
        "Hisabi Transaction Allocation",
        "Hisabi Budget",
        "Hisabi Goal",
        "Hisabi Debt",
        "Hisabi Debt Installment",
        "Hisabi Debt Request",
        "Hisabi Jameya",
        "Hisabi Jameya Payment",
        "Hisabi FX Rate",
        "Hisabi Custom Currency",
        "Hisabi Attachment",
        "Hisabi Audit Log",
    ]

    # Identify users that have any Hisabi docs missing wallet_id.
    users: set[str] = set()
    for dt in doctypes:
        if not frappe.db.exists("DocType", dt):
            continue
        meta = frappe.get_meta(dt)
        if not meta.has_field("wallet_id") or not meta.has_field("user"):
            continue
        rows = frappe.db.sql(
            f"""
            SELECT DISTINCT user
            FROM `tab{dt}`
            WHERE (wallet_id IS NULL OR wallet_id='' ) AND is_deleted=0
            """,
            as_dict=True,
        )
        for r in rows:
            if r.user:
                users.add(r.user)

    now = now_datetime()

    for user in users:
        wallet_id = _default_wallet_id_for_user(user)

        if not frappe.db.exists("Hisabi Wallet", wallet_id):
            w = frappe.new_doc("Hisabi Wallet")
            w.client_id = wallet_id
            w.wallet_name = "Default Wallet"
            w.status = "active"
            w.owner_user = user
            apply_common_sync_fields(w, bump_version=True, mark_deleted=False)
            w.save(ignore_permissions=True)

        if not frappe.db.exists("Hisabi Wallet Member", {"wallet": wallet_id, "user": user}):
            m = frappe.new_doc("Hisabi Wallet Member")
            m.wallet = wallet_id
            m.user = user
            m.role = "owner"
            m.status = "active"
            m.joined_at = now
            apply_common_sync_fields(m, bump_version=True, mark_deleted=False)
            m.save(ignore_permissions=True)

        # Backfill all docs for this user into that wallet.
        for dt in doctypes:
            if not frappe.db.exists("DocType", dt):
                continue
            meta = frappe.get_meta(dt)
            if not meta.has_field("wallet_id") or not meta.has_field("user"):
                continue
            frappe.db.sql(
                f"""
                UPDATE `tab{dt}`
                SET wallet_id=%s
                WHERE user=%s AND (wallet_id IS NULL OR wallet_id='')
                """,
                (wallet_id, user),
            )

    frappe.db.commit()

