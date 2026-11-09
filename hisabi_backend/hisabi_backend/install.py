"""Installation and test hooks for Hisabi Backend."""

import frappe


ROLES = ("Hisabi User", "Hisabi Admin")

# Hisabi app users are mobile/website users, NOT desk users. A Role with desk_access=1 makes
# Frappe's User doctype auto-promote every holder to "System User" (desk access + a license
# seat) on save — even though register_user sets user_type="Website User". So the mass
# app-user role must have desk_access=0.
ROLE_DESK_ACCESS = {"Hisabi User": 0, "Hisabi Admin": 1}


def ensure_roles() -> None:
    """Create required roles if missing, and keep desk_access correct (also on existing roles)."""
    for role in ROLES:
        desk_access = ROLE_DESK_ACCESS.get(role, 1)
        if frappe.db.exists("Role", role):
            # Correct a role that was previously created with the wrong desk_access.
            if frappe.db.get_value("Role", role, "desk_access") != desk_access:
                frappe.db.set_value("Role", role, "desk_access", desk_access)
            continue

        role_doc = frappe.get_doc({
            "doctype": "Role",
            "role_name": role,
            "desk_access": desk_access,
        })
        role_doc.insert(ignore_permissions=True)


def after_install() -> None:
    ensure_roles()


def after_migrate() -> None:
    # Runs on every `bench migrate` (the BildFast pipeline applies migrations) so role
    # desk_access corrections reach existing sites, not just fresh installs.
    ensure_roles()


def before_tests() -> None:
    ensure_roles()
    from hisabi_backend.tests.bootstrap import run_test_bootstrap

    run_test_bootstrap()
