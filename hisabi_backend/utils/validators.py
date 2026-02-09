"""Validation helpers for sync and API input."""

import re

import frappe
from frappe import _

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
PHONE_ALLOWED_RE = re.compile(r"^\+?[0-9]+$")
PHONE_MIN_DIGITS = 8
PHONE_MAX_DIGITS = 15


def validate_client_id(client_id: str) -> str:
    """Validate and normalize client_id.

    Expected format: 3-128 chars, starts with alnum, contains alnum/_/-.
    """
    if not client_id:
        frappe.throw(_("client_id is required"), frappe.ValidationError)

    client_id = client_id.strip()
    if not CLIENT_ID_RE.match(client_id):
        frappe.throw(_("Invalid client_id format"), frappe.ValidationError)

    return client_id


def validate_platform(platform: str) -> str:
    """Validate and normalize platform values."""
    if not platform:
        frappe.throw(_("platform is required"), frappe.ValidationError)

    platform = platform.strip().lower()
    allowed = {"android", "ios", "web"}
    if platform not in allowed:
        frappe.throw(_("Invalid platform"), frappe.ValidationError)

    return platform


def normalize_phone(phone: str) -> str:
    """Normalize phone by removing spaces and punctuation."""
    if not phone:
        frappe.throw(_("phone is required"), frappe.ValidationError)
    phone = phone.strip()
    if phone.startswith("+"):
        prefix = "+"
        digits = re.sub(r"\D", "", phone[1:])
        return prefix + digits
    return re.sub(r"\D", "", phone)


def normalize_and_validate_phone(phone: str) -> str:
    """Normalize and validate phone with optional leading '+'.

    Rules:
    - trim outer whitespace
    - remove spaces/dashes
    - allow only digits after optional leading '+'
    - enforce E.164-style digit count (8..15)
    """
    if not phone:
        frappe.throw(_("phone is required"), frappe.ValidationError)

    raw = phone.strip().replace(" ", "").replace("-", "")
    if not raw:
        frappe.throw(_("phone is required"), frappe.ValidationError)

    has_plus = raw.startswith("+")
    digits = raw[1:] if has_plus else raw
    # Auth: keep phone validation consistent across register/login and test scripts.
    if not digits or not PHONE_ALLOWED_RE.match(raw):
        frappe.throw(_("Invalid phone"), frappe.ValidationError)
    if len(digits) < PHONE_MIN_DIGITS or len(digits) > PHONE_MAX_DIGITS:
        frappe.throw(_("Invalid phone length"), frappe.ValidationError)

    return f"+{digits}" if has_plus else digits


def validate_currency(currency: str, user: str | None = None) -> str:
    """Validate currency code against Currency or user custom currencies."""
    if not currency:
        frappe.throw(_("currency is required"), frappe.ValidationError)

    currency = currency.strip().upper()
    if frappe.db.exists("Currency", currency):
        return currency

    filters = {"code": currency, "is_deleted": 0}
    if user:
        filters["user"] = user
    if frappe.db.exists("Hisabi Custom Currency", filters):
        return currency

    frappe.throw(_("Invalid currency: {0}").format(currency), frappe.ValidationError)
    return currency


def validate_password_strength(password: str) -> None:
    """Validate password strength (minimum length)."""
    if not password or len(password) < 8:
        frappe.throw(_("Password must be at least 8 characters"), frappe.ValidationError)


def ensure_entity_id_matches(entity_id: str | None, client_id: str | None) -> None:
    """Ensure entity_id matches client_id when both are provided."""
    if entity_id and client_id and entity_id != client_id:
        frappe.throw(_("entity_id must equal client_id"), frappe.ValidationError)


def ensure_base_version(base_version: object) -> bool:
    """Return True if base_version is present for update/delete operations."""
    return base_version is not None


LINK_OWNERSHIP_FIELDS = {
    "Hisabi Transaction": {
        "account": "Hisabi Account",
        "to_account": "Hisabi Account",
        "category": "Hisabi Category",
        "bucket": "Hisabi Bucket",
        "budget": "Hisabi Budget",
        "goal": "Hisabi Goal",
        "debt": "Hisabi Debt",
        "jameya": "Hisabi Jameya",
    },
    "Hisabi Allocation Rule Line": {"rule": "Hisabi Allocation Rule", "bucket": "Hisabi Bucket"},
    "Hisabi Transaction Allocation": {"transaction": "Hisabi Transaction", "bucket": "Hisabi Bucket"},
    "Hisabi Transaction Bucket": {"transaction_id": "Hisabi Transaction", "bucket_id": "Hisabi Bucket"},
    "Hisabi Budget": {"account": "Hisabi Account", "category": "Hisabi Category"},
    "Hisabi Goal": {"account": "Hisabi Account"},
    "Hisabi Debt": {"account": "Hisabi Account"},
    "Hisabi Debt Installment": {"debt": "Hisabi Debt"},
    "Hisabi Debt Request": {"debt": "Hisabi Debt"},
    "Hisabi Jameya Payment": {"jameya": "Hisabi Jameya", "account": "Hisabi Account"},
    "Hisabi Attachment": {"transaction": "Hisabi Transaction"},
}


def ensure_link_ownership(doctype: str, payload: dict, user: str, wallet_id: str | None = None) -> None:
    """Ensure linked documents are within the same wallet (and belong to user when wallet is absent).

    Backward compatible: older deployments filtered by user ownership only.
    For shared wallets, wallet_id is authoritative.
    """
    field_map = LINK_OWNERSHIP_FIELDS.get(doctype, {})
    if not field_map or not payload:
        return

    for fieldname, link_doctype in field_map.items():
        value = payload.get(fieldname)
        if not value:
            continue

        meta = frappe.get_meta(link_doctype)
        if meta.has_field("wallet_id") and wallet_id:
            link_wallet = frappe.get_value(link_doctype, value, "wallet_id")
            if not link_wallet:
                frappe.throw(_("{0} not found").format(link_doctype), frappe.ValidationError)
            if link_wallet != wallet_id:
                frappe.throw(_("{0} is not in this wallet").format(link_doctype), frappe.PermissionError)
        else:
            owner_field = "user" if meta.has_field("user") else "owner"
            owner = frappe.get_value(link_doctype, value, owner_field)
            if not owner:
                frappe.throw(_("{0} not found").format(link_doctype), frappe.ValidationError)
            if owner != user:
                frappe.throw(_("{0} does not belong to user").format(link_doctype), frappe.PermissionError)
