from __future__ import annotations

import frappe


def execute() -> None:
    """Backfill Hisabi User.default_wallet based on oldest wallet membership."""
    if not frappe.db.exists("DocType", "Hisabi User"):
        return
    if not frappe.db.exists("DocType", "Hisabi Wallet Member"):
        return

    users = frappe.db.sql(
        """
        SELECT user
        FROM `tabHisabi User`
        WHERE IFNULL(default_wallet, '') = '' AND IFNULL(user, '') != ''
        """,
        as_dict=True,
    )
    if not users:
        return

    for row in users:
        user = row.user
        if not user:
            continue

        member = frappe.db.sql(
            """
            SELECT wallet
            FROM `tabHisabi Wallet Member`
            WHERE user=%s AND status!='removed'
            ORDER BY IFNULL(joined_at, creation) ASC, creation ASC
            LIMIT 1
            """,
            (user,),
            as_dict=True,
        )
        if not member:
            continue

        wallet_id = member[0].wallet
        if not wallet_id:
            continue

        name = frappe.get_value("Hisabi User", {"user": user})
        if not name:
            continue

        frappe.db.set_value("Hisabi User", name, "default_wallet", wallet_id, update_modified=False)

    frappe.db.commit()
