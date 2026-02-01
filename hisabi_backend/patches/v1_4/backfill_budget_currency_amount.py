import frappe


def _resolve_base_currency(wallet_id: str | None, user: str | None) -> str | None:
    if wallet_id:
        currency = frappe.get_value(
            "Hisabi Settings",
            {"wallet_id": wallet_id, "is_deleted": 0},
            "base_currency",
        )
        if currency:
            return currency
    if user:
        currency = frappe.get_value(
            "Hisabi Settings",
            {"user": user, "is_deleted": 0},
            "base_currency",
        )
        if currency:
            return currency
    return frappe.db.get_single_value("System Settings", "currency")


def execute() -> None:
    if not frappe.db.exists("DocType", "Hisabi Budget"):
        return

    rows = frappe.get_all(
        "Hisabi Budget",
        fields=["name", "wallet_id", "user", "currency", "amount", "amount_base"],
    )

    for row in rows:
        updates = {}
        if not row.get("currency"):
            base_currency = _resolve_base_currency(row.get("wallet_id"), row.get("user"))
            if base_currency:
                updates["currency"] = base_currency
        amount = row.get("amount")
        amount_base = row.get("amount_base")
        if (amount is None or amount == 0) and amount_base is not None:
            updates["amount"] = amount_base

        if updates:
            frappe.db.set_value("Hisabi Budget", row["name"], updates)
