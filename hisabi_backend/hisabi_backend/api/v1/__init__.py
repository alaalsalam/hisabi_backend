"""V1 API package for Hisabi Backend.

This file also provides thin wrappers for stable method paths like:
`/api/method/hisabi_backend.api.v1.wallets_list`
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe


@frappe.whitelist(allow_guest=True)
def register_user(
    email: str | None = None,
    phone: str | None = None,
    full_name: str | None = None,
    password: str | None = None,
    device: dict | None = None,
):
    from .auth_v2 import register_user as _impl

    return _impl(email=email, phone=phone, full_name=full_name, password=password, device=device)


@frappe.whitelist(allow_guest=True)
def login(identifier: str, password: str, device: dict | None = None):
    from .auth_v2 import login as _impl

    return _impl(identifier=identifier, password=password, device=device)


@frappe.whitelist(allow_guest=False)
def logout():
    from .auth_v2 import logout as _impl

    return _impl()


@frappe.whitelist(allow_guest=False)
def me():
    from .auth_v2 import me as _impl

    return _impl()


@frappe.whitelist(allow_guest=False)
def device_revoke(device_id: str):
    from .auth_v2 import device_revoke as _impl

    return _impl(device_id=device_id)


@frappe.whitelist(allow_guest=False)
def devices_list():
    from .devices import devices_list as _impl

    return _impl()


@frappe.whitelist(allow_guest=False)
def wallets_list(device_id: Optional[str] = None) -> Dict[str, Any]:
    from .wallets import wallets_list as _impl

    return _impl(device_id=device_id)


@frappe.whitelist(allow_guest=False)
def wallet_create(client_id: str, wallet_name: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    from .wallets import wallet_create as _impl

    return _impl(client_id=client_id, wallet_name=wallet_name, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def wallet_invite_create(
    wallet_id: str,
    role_to_grant: str = "member",
    target_phone: Optional[str] = None,
    target_email: Optional[str] = None,
    expires_in_hours: int = 72,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .wallets import wallet_invite_create as _impl

    return _impl(
        wallet_id=wallet_id,
        role_to_grant=role_to_grant,
        target_phone=target_phone,
        target_email=target_email,
        expires_in_hours=expires_in_hours,
        device_id=device_id,
    )


@frappe.whitelist(allow_guest=False)
def wallet_invite_accept(
    invite_code: Optional[str] = None, token: Optional[str] = None, device_id: Optional[str] = None
) -> Dict[str, Any]:
    from .wallets import wallet_invite_accept as _impl

    return _impl(invite_code=invite_code, token=token, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def wallet_member_remove(wallet_id: str, user_to_remove: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    from .wallets import wallet_member_remove as _impl

    return _impl(wallet_id=wallet_id, user_to_remove=user_to_remove, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def wallet_leave(wallet_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    from .wallets import wallet_leave as _impl

    return _impl(wallet_id=wallet_id, device_id=device_id)
