import frappe


def execute() -> None:
    if not frappe.db.exists("DocType", "Hisabi Debt Request"):
        return
    frappe.db.sql(
        """
        UPDATE `tabHisabi Debt Request`
        SET status='rejected'
        WHERE status='declined'
        """
    )
