"""Installation and test hooks for Hisabi Backend."""

import frappe


ROLES = ("Hisabi User", "Hisabi Admin")


def ensure_roles() -> None:
    """Create required roles if they do not exist."""
    for role in ROLES:
        if frappe.db.exists("Role", role):
            continue

        role_doc = frappe.get_doc({
            "doctype": "Role",
            "role_name": role,
        })
        role_doc.insert(ignore_permissions=True)


def after_install() -> None:
    ensure_roles()


def before_tests() -> None:
    ensure_roles()
