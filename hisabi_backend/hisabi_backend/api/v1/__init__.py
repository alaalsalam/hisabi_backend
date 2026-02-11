"""V1 API package for Hisabi Backend.

This file also provides thin wrappers for stable method paths like:
`/api/method/hisabi_backend.api.v1.wallets_list`
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from hisabi_backend.utils.api_errors import error_response
from hisabi_backend.utils.request_headers import strip_expect_header


def _serialize_user(user: str) -> Dict[str, Any]:
    u = frappe.get_doc("User", user)
    return {
        "name": u.name,
        "full_name": u.full_name,
        "email": u.email,
        "phone": getattr(u, "phone", None) or getattr(u, "mobile_no", None),
    }


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
    strip_expect_header()
    try:
        return _impl(identifier=identifier, password=password, device=device)
    except frappe.ValidationError as exc:
        return error_response(
            status_code=400,
            code="VALIDATION_ERROR",
            message=str(exc),
            user_message="بيانات غير صحيحة. يرجى التحقق والمحاولة مرة أخرى.",
            action="retry",
        )
    except frappe.AuthenticationError as exc:
        return error_response(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message=str(exc),
            user_message="بيانات الدخول غير صحيحة.",
            action="retry",
        )
    except frappe.PermissionError as exc:
        return error_response(
            status_code=403,
            code="FORBIDDEN",
            message=str(exc),
            user_message="غير مصرح.",
            action="contact_support",
        )


@frappe.whitelist(allow_guest=False)
def logout():
    from .auth_v2 import logout as _impl

    return _impl()


@frappe.whitelist(allow_guest=False)
def me():
    strip_expect_header()
    # Keep response shape stable for clients: include default_wallet_id + wallets[].isDefault.
    from frappe.utils import now_datetime

    from hisabi_backend.utils.security import require_device_token_auth
    from hisabi_backend.utils.wallet_acl import ensure_default_wallet_for_user, get_wallets_for_user

    try:
        user, device = require_device_token_auth()
        default_wallet_id = ensure_default_wallet_for_user(user, device_id=device.device_id)
        wallets = get_wallets_for_user(user, default_wallet_id=default_wallet_id)
        # Defensive: ensure the derived flag exists even if helper output changes.
        for row in wallets:
            row["isDefault"] = row.get("wallet") == default_wallet_id
        return {
            "user": _serialize_user(user),
            "device": {"device_id": device.device_id, "status": device.status, "last_seen_at": device.last_seen_at},
            "default_wallet_id": default_wallet_id,
            "wallets": wallets,
            "server_time": now_datetime().isoformat(),
        }
    except frappe.ValidationError as exc:
        return error_response(
            status_code=400,
            code="VALIDATION_ERROR",
            message=str(exc),
            user_message="طلب غير صالح.",
            action="retry",
        )
    except frappe.AuthenticationError as exc:
        return error_response(
            status_code=401,
            code="UNAUTHORIZED",
            message=str(exc),
            user_message="تحتاج إلى تسجيل الدخول.",
            action="retry",
        )
    except frappe.PermissionError as exc:
        return error_response(
            status_code=403,
            code="FORBIDDEN",
            message=str(exc),
            user_message="غير مصرح.",
            action="contact_support",
        )


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


@frappe.whitelist(allow_guest=False)
def bucket_expenses_set(
    transaction_id: str,
    bucket_id: str,
    wallet_id: Optional[str] = None,
    client_id: Optional[str] = None,
    op_id: Optional[str] = None,
    base_version: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .bucket_expenses import set as _impl

    return _impl(
        transaction_id=transaction_id,
        bucket_id=bucket_id,
        wallet_id=wallet_id,
        client_id=client_id,
        op_id=op_id,
        base_version=base_version,
        device_id=device_id,
    )


@frappe.whitelist(allow_guest=False)
def bucket_expenses_clear(
    transaction_id: str,
    wallet_id: Optional[str] = None,
    op_id: Optional[str] = None,
    base_version: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .bucket_expenses import clear as _impl

    return _impl(
        transaction_id=transaction_id,
        wallet_id=wallet_id,
        op_id=op_id,
        base_version=base_version,
        device_id=device_id,
    )


@frappe.whitelist(allow_guest=False)
def recurring_rules_list(wallet_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
    from .recurring import rules_list as _impl

    return _impl(wallet_id=wallet_id, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def recurring_rules_upsert(**kwargs):
    from .recurring import upsert_rule as _impl

    return _impl(**kwargs)


@frappe.whitelist(allow_guest=False)
def recurring_rule_toggle(
    rule_id: str,
    wallet_id: Optional[str] = None,
    is_active: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import toggle_rule as _impl

    return _impl(rule_id=rule_id, wallet_id=wallet_id, is_active=is_active, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def recurring_generate(
    wallet_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    dry_run: Optional[int] = 0,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import generate as _impl

    return _impl(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        dry_run=dry_run,
        device_id=device_id,
    )


@frappe.whitelist(allow_guest=False)
def recurring_apply_changes(
    rule_id: str,
    wallet_id: Optional[str] = None,
    mode: Optional[str] = None,
    from_date: Optional[str] = None,
    horizon_days: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import apply_changes as _impl

    return _impl(
        rule_id=rule_id,
        wallet_id=wallet_id,
        mode=mode,
        from_date=from_date,
        horizon_days=horizon_days,
        device_id=device_id,
    )


@frappe.whitelist(allow_guest=False)
def recurring_instance_skip(
    instance_id: str,
    wallet_id: Optional[str] = None,
    reason: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import skip_instance as _impl

    return _impl(instance_id=instance_id, wallet_id=wallet_id, reason=reason, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def recurring_rule_pause_until(
    rule_id: str,
    until_date: str,
    wallet_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import pause_until as _impl

    return _impl(rule_id=rule_id, until_date=until_date, wallet_id=wallet_id, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def recurring_preview(
    wallet_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import preview as _impl

    return _impl(wallet_id=wallet_id, rule_id=rule_id, from_date=from_date, to_date=to_date, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def recurring_due(
    wallet_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import due as _impl

    return _impl(wallet_id=wallet_id, from_date=from_date, to_date=to_date, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def recurring_generate_due(
    wallet_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    mode: Optional[str] = "create_missing",
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .recurring import generate_due as _impl

    return _impl(wallet_id=wallet_id, from_date=from_date, to_date=to_date, mode=mode, device_id=device_id)


@frappe.whitelist(allow_guest=False)
def backup_export(wallet_id: Optional[str] = None, format: str = "hisabi_json_v1"):
    from .backup import export as _impl

    return _impl(wallet_id=wallet_id, format=format)


@frappe.whitelist(allow_guest=False)
def backup_validate_restore(wallet_id: Optional[str] = None, payload: dict | None = None):
    from .backup import validate_restore as _impl

    return _impl(wallet_id=wallet_id, payload=payload)


@frappe.whitelist(allow_guest=False)
def backup_apply_restore(wallet_id: Optional[str] = None, payload: dict | None = None, mode: str = "merge"):
    from .backup import apply_restore as _impl

    return _impl(wallet_id=wallet_id, payload=payload, mode=mode)


@frappe.whitelist(allow_guest=False)
def review_issues(
    wallet_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    include_resolved: Optional[int] = 0,
    device_id: Optional[str] = None,
):
    from .review import issues as _impl

    return _impl(
        wallet_id=wallet_id,
        from_date=from_date,
        to_date=to_date,
        include_resolved=include_resolved,
        device_id=device_id,
    )


@frappe.whitelist(allow_guest=False)
def review_apply_fix(
    wallet_id: Optional[str] = None,
    fixes: Optional[list[dict]] = None,
    device_id: Optional[str] = None,
):
    from .review import apply_fix as _impl

    return _impl(wallet_id=wallet_id, fixes=fixes, device_id=device_id)
