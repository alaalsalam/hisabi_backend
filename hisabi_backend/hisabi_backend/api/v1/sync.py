"""Sync endpoints (v1)."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime
from werkzeug.wrappers import Response
from hisabi_backend.domain.recalc_engine import (
    recalc_account_balance,
    recalc_budgets,
    recalc_debts,
    recalc_goals,
    recalc_jameyas,
)
from hisabi_backend.utils.security import require_device_auth
from hisabi_backend.utils.fx_defaults import seed_wallet_default_fx_rates
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.wallet_acl import require_wallet_member
from hisabi_backend.utils.validators import (
    ensure_entity_id_matches,
    ensure_link_ownership,
    validate_client_id,
)


DOCTYPE_LIST = [
    "Hisabi Settings",
    "Hisabi Device",
    "Hisabi Wallet",
    "Hisabi Wallet Member",
    "Hisabi Account",
    "Hisabi Category",
    "Hisabi Transaction",
    "Hisabi Attachment",
    "Hisabi Bucket",
    "Hisabi Bucket Template",
    "Hisabi Allocation Rule",
    "Hisabi Allocation Rule Line",
    "Hisabi Transaction Allocation",
    "Hisabi Transaction Bucket",
    "Hisabi Transaction Bucket Expense",
    "Hisabi Recurring Rule",
    "Hisabi Recurring Instance",
    "Hisabi Budget",
    "Hisabi Goal",
    "Hisabi Debt",
    "Hisabi Debt Installment",
    "Hisabi Debt Request",
    "Hisabi Jameya",
    "Hisabi Jameya Payment",
    "Hisabi FX Rate",
    "Hisabi Custom Currency",
    "Hisabi Audit Log",
]

SYNC_PUSH_ALLOWLIST = {
    "Hisabi Wallet",
    "Hisabi Wallet Member",
    "Hisabi Settings",
    "Hisabi FX Rate",
    "Hisabi Custom Currency",
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
}

# Security: prevent client from writing unexpected fields / doctypes through sync.
SYNC_PUSH_ALLOWED_FIELDS = {
    "Hisabi Wallet": {
        "client_id",
        "wallet_id",
        "wallet_name",
        "status",
        "client_created_ms",
        "client_modified_ms",
    },
    "Hisabi Wallet Member": {
        "client_id",
        "wallet_id",
        "wallet",
        "user",
        "role",
        "status",
        "joined_at",
        "removed_at",
        "client_created_ms",
        "client_modified_ms",
    },
    "Hisabi Settings": {
        "client_id",
        "wallet_id",
        "user_name",
        "base_currency",
        "enabled_currencies",
        "locale",
        "phone_number",
        "notifications_preferences",
        "enforce_fx",
        "week_start_day",
        "use_arabic_numerals",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi FX Rate": {
        "client_id",
        "wallet_id",
        "base_currency",
        "quote_currency",
        "rate",
        "effective_date",
        "source",
        "last_updated",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Custom Currency": {
        "client_id",
        "wallet_id",
        "code",
        "name_ar",
        "name_en",
        "symbol",
        "decimals",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Account": {
        "client_id",
        "wallet_id",
        "account_name",
        "account_type",
        "currency",
        "is_multi_currency",
        "base_currency",
        "group_id",
        "parent_account",
        "opening_balance",
        "color",
        "icon",
        "archived",
        "sort_order",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Category": {
        "client_id",
        "wallet_id",
        "category_name",
        "kind",
        "parent_category",
        "color",
        "icon",
        "archived",
        "sort_order",
        "default_bucket",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Transaction": {
        "client_id",
        "wallet_id",
        "transaction_type",
        "date_time",
        "amount",
        "currency",
        "original_amount",
        "original_currency",
        "converted_amount",
        "account",
        "to_account",
        "category",
        "bucket",
        "note",
        "amount_base",
        "base_amount",
        "fx_rate",
        "fx_rate_used",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Debt": {
        "client_id",
        "wallet_id",
        "debt_name",
        "direction",
        "currency",
        "principal_amount",
        "counterparty_name",
        "counterparty_type",
        "counterparty_phone",
        "confirmed",
        "note",
        "due_date",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Debt Installment": {
        "client_id",
        "wallet_id",
        "debt",
        "due_date",
        "amount",
        "status",
        "paid_at",
        "paid_amount",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Debt Request": {
        "client_id",
        "wallet_id",
        "from_phone",
        "to_phone",
        "debt_payload",
        "debt_payload_json",
        "status",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Budget": {
        "client_id",
        "wallet_id",
        "budget_name",
        "period",
        "scope_type",
        "category",
        "currency",
        "amount",
        "amount_base",
        "start_date",
        "end_date",
        "alert_threshold",
        "archived",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Goal": {
        "client_id",
        "wallet_id",
        "goal_name",
        "goal_type",
        "currency",
        "target_amount",
        "target_amount_base",
        "target_date",
        "linked_account",
        "linked_debt",
        "status",
        "color",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Bucket": {
        "client_id",
        "wallet_id",
        "title",
        "bucket_name",
        "color",
        "icon",
        "sort_order",
        "is_active",
        "archived",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Bucket Template": {
        "client_id",
        "wallet_id",
        "title",
        "is_default",
        "is_active",
        "template_items",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Allocation Rule": {
        "client_id",
        "wallet_id",
        "rule_name",
        "is_default",
        "scope_type",
        "scope_ref",
        "active",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Allocation Rule Line": {
        "client_id",
        "wallet_id",
        "rule",
        "bucket",
        "percent",
        "sort_order",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Transaction Allocation": {
        "client_id",
        "wallet_id",
        "transaction_id",
        "bucket_id",
        "percentage",
        "transaction",
        "bucket",
        "percent",
        "amount",
        "currency",
        "amount_base",
        "rule_used",
        "is_manual_override",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Transaction Bucket": {
        "client_id",
        "wallet_id",
        "transaction_id",
        "bucket_id",
        "amount",
        "percentage",
        "transaction",
        "bucket",
        "percent",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Transaction Bucket Expense": {
        "client_id",
        "wallet_id",
        "transaction_id",
        "bucket_id",
        "transaction",
        "bucket",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Recurring Rule": {
        "client_id",
        "wallet_id",
        "is_active",
        "title",
        "transaction_type",
        "amount",
        "currency",
        "category_id",
        "account_id",
        "note",
        "start_date",
        "timezone",
        "rrule_type",
        "interval",
        "byweekday",
        "bymonthday",
        "end_mode",
        "until_date",
        "resume_date",
        "count",
        "last_generated_at",
        "created_from",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Recurring Instance": {
        "client_id",
        "wallet_id",
        "rule_id",
        "occurrence_date",
        "transaction_id",
        "status",
        "generated_at",
        "skip_reason",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Jameya": {
        "client_id",
        "wallet_id",
        "jameya_name",
        "currency",
        "monthly_amount",
        "total_members",
        "my_turn",
        "total_amount",
        "period",
        "start_date",
        "status",
        "note",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Jameya Payment": {
        "client_id",
        "wallet_id",
        "jameya",
        "period_number",
        "due_date",
        "amount",
        "status",
        "paid_at",
        "is_my_turn",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
    "Hisabi Attachment": {
        "client_id",
        "wallet_id",
        "owner_entity_type",
        "owner_client_id",
        "file_id",
        "file_url",
        "file_name",
        "mime_type",
        "file_size",
        "sha256",
        "client_created_ms",
        "client_modified_ms",
        "is_deleted",
        "deleted_at",
    },
}

SYNC_PUSH_REQUIRED_FIELDS_CREATE = {
    "Hisabi Wallet": {"wallet_name", "status"},
    "Hisabi Wallet Member": {"wallet", "user", "role", "status"},
    "Hisabi Settings": {"base_currency"},
    "Hisabi FX Rate": {"base_currency", "quote_currency", "rate"},
    "Hisabi Custom Currency": {"code"},
    "Hisabi Account": {"account_name", "account_type"},
    "Hisabi Category": {"category_name", "kind"},
    "Hisabi Transaction": {"transaction_type", "date_time", "amount", "currency", "account"},
    "Hisabi Debt": {"debt_name", "direction", "principal_amount"},
    "Hisabi Debt Installment": {"debt", "amount"},
    "Hisabi Debt Request": set(),
    "Hisabi Budget": {"budget_name", "period", "scope_type"},
    "Hisabi Goal": {"goal_name", "goal_type"},
    "Hisabi Bucket": set(),
    "Hisabi Bucket Template": {"title", "template_items"},
    "Hisabi Allocation Rule": {"rule_name", "scope_type"},
    "Hisabi Allocation Rule Line": {"rule", "bucket"},
    "Hisabi Transaction Allocation": {"transaction", "bucket"},
    "Hisabi Transaction Bucket": set(),
    "Hisabi Transaction Bucket Expense": {"transaction_id", "bucket_id"},
    "Hisabi Recurring Rule": {"title", "transaction_type", "amount", "currency", "start_date", "rrule_type"},
    "Hisabi Recurring Instance": {"rule_id", "occurrence_date", "status"},
    "Hisabi Jameya": {"jameya_name", "monthly_amount", "total_members", "my_turn", "start_date"},
    "Hisabi Jameya Payment": {"jameya"},
    "Hisabi Attachment": {"owner_entity_type", "owner_client_id", "file_mime", "file_size"},
}

SYNC_PUSH_REQUIRED_FIELD_GROUPS = {
    "Hisabi Bucket": [{"title", "bucket_name"}],
    "Hisabi Bucket Template": [{"template_items"}],
    "Hisabi Budget": [{"amount", "amount_base"}],
    "Hisabi Goal": [{"target_amount", "target_amount_base"}],
    "Hisabi Transaction Bucket": [{"transaction_id", "transaction"}, {"bucket_id", "bucket"}, {"amount", "percentage", "percent"}],
    "Hisabi Transaction Bucket Expense": [{"transaction_id", "transaction"}, {"bucket_id", "bucket"}],
    "Hisabi Recurring Rule": [{"title"}],
    "Hisabi Recurring Instance": [{"rule_id"}, {"occurrence_date"}],
}

SYNC_PUSH_FIELD_TYPES = {
    "wallet_name": "string",
    "status": "string",
    "wallet": "string",
    "user": "string",
    "role": "string",
    "user_name": "string",
    "base_currency": "string",
    "quote_currency": "string",
    "enabled_currencies": "list",
    "locale": "string",
    "phone_number": "string",
    "notifications_preferences": "json",
    "source": "string",
    "code": "string",
    "name_ar": "string",
    "name_en": "string",
    "symbol": "string",
    "account_name": "string",
    "account_type": "string",
    "currency": "string",
    "group_id": "string",
    "parent_account": "string",
    "category_name": "string",
    "kind": "string",
    "transaction_type": "string",
    "date_time": "string",
    "account": "string",
    "debt_name": "string",
    "direction": "string",
    "budget_name": "string",
    "period": "string",
    "scope_type": "string",
    "goal_name": "string",
    "goal_type": "string",
    "title": "string",
    "bucket_name": "string",
    "template_items": "list",
    "rule_name": "string",
    "transaction_id": "string",
    "bucket_id": "string",
    "transaction": "string",
    "jameya_name": "string",
    "start_date": "string",
    "owner_entity_type": "string",
    "owner_client_id": "string",
    "file_mime": "string",
    "rrule_type": "string",
    "byweekday": "string",
    "end_mode": "string",
    "occurrence_date": "string",
    "generated_at": "string",
    "skip_reason": "string",
    "amount": "number",
    "rate": "number",
    "amount_base": "number",
    "base_amount": "number",
    "principal_amount": "number",
    "target_amount": "number",
    "target_amount_base": "number",
    "monthly_amount": "number",
    "total_members": "number",
    "my_turn": "number",
    "file_size": "number",
    "original_amount": "number",
    "converted_amount": "number",
    "fx_rate_used": "number",
    "fx_rate": "number",
    "percentage": "number",
    "is_active": "number",
    "is_default": "number",
    "interval": "number",
    "bymonthday": "number",
    "count": "number",
    "week_start_day": "number",
    "use_arabic_numerals": "number",
    "enforce_fx": "number",
    "decimals": "number",
    "original_currency": "string",
    "is_multi_currency": "number",
}

SENSITIVE_SYNC_FIELDS = {
    "password",
    "pwd",
    "passcode",
    "device_token",
    "device_token_hash",
}

SYNC_PAYLOAD_LOG_IGNORE_KEYS = {"id"}

RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW_SEC = 600
MAX_PUSH_ITEMS = 200
MAX_PAYLOAD_BYTES = 100 * 1024

SERVER_AUTH_FIELDS = {
    "Hisabi Account": {"current_balance"},
    "Hisabi Budget": {"spent_amount"},
    "Hisabi Goal": {"current_amount", "progress_percent", "remaining_amount"},
    "Hisabi Debt": {"remaining_amount", "status"},
    "Hisabi Jameya": {"status", "total_amount"},
}

SYNC_PULL_BASE_FIELDS = {"client_id", "doc_version", "server_modified", "is_deleted", "deleted_at"}
# Contract: pull payload must be whitelisted to avoid leaking internal server fields.
SYNC_PULL_ALLOWED_FIELDS = {
    doctype: set(fields) | set(SERVER_AUTH_FIELDS.get(doctype, set())) | set(SYNC_PULL_BASE_FIELDS)
    for doctype, fields in SYNC_PUSH_ALLOWED_FIELDS.items()
}
SYNC_PULL_SYSTEM_FIELDS = {
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "doctype",
    "_user_tags",
    "_comments",
    "_assign",
    "_liked_by",
}

FIELD_MAP = {
    "Hisabi Settings": {
        "default_currency": "base_currency",
        "defaultCurrency": "base_currency",
        "baseCurrency": "base_currency",
        "enabledCurrencies": "enabled_currencies",
        "phoneNumber": "phone_number",
        "notificationPreferences": "notifications_preferences",
        "notificationsPreferences": "notifications_preferences",
        "enforceFx": "enforce_fx",
        "weekStartDay": "week_start_day",
        "useArabicNumerals": "use_arabic_numerals",
    },
    "Hisabi FX Rate": {
        "from_currency": "base_currency",
        "to_currency": "quote_currency",
        "fromCurrency": "base_currency",
        "toCurrency": "quote_currency",
        "updated_at": "last_updated",
    },
    "Hisabi Custom Currency": {
        "name": "name_en",
    },
    "Hisabi Account": {
        "name": "account_name",
        "title": "account_name",
        "type": "account_type",
        "isMultiCurrency": "is_multi_currency",
        "baseCurrency": "base_currency",
        "groupId": "group_id",
        "parentAccount": "parent_account",
        "parentAccountId": "parent_account",
        "parent_account_id": "parent_account",
    },
    "Hisabi Category": {
        "name": "category_name",
        "title": "category_name",
        "parent_id": "parent_category",
        "default_bucket_id": "default_bucket",
    },
    "Hisabi Bucket": {
        "name": "title",
        "bucket_name": "title",
    },
    "Hisabi Bucket Template": {
        "name": "title",
    },
    "Hisabi Allocation Rule": {
        "name": "rule_name",
        "title": "rule_name",
        "scope_ref_id": "scope_ref",
    },
    "Hisabi Allocation Rule Line": {
        "rule_id": "rule",
        "bucket_id": "bucket",
    },
    "Hisabi Transaction Allocation": {
        "transaction_id": "transaction",
        "bucket_id": "bucket",
        "percentage": "percent",
        "rule_id_used": "rule_used",
    },
    "Hisabi Transaction Bucket": {
        "transaction": "transaction_id",
        "bucket": "bucket_id",
        "percent": "percentage",
    },
    "Hisabi Transaction Bucket Expense": {
        "transaction": "transaction_id",
        "bucket": "bucket_id",
    },
    "Hisabi Recurring Rule": {
        "category": "category_id",
        "account": "account_id",
    },
    "Hisabi Recurring Instance": {
        "rule": "rule_id",
        "transaction": "transaction_id",
    },
    "Hisabi Budget": {
        "name": "budget_name",
        "title": "budget_name",
        "category_id": "category",
    },
    "Hisabi Goal": {
        "name": "goal_name",
        "title": "goal_name",
        "type": "goal_type",
        "linked_account_id": "linked_account",
        "linked_debt_id": "linked_debt",
    },
    "Hisabi Debt": {"name": "debt_name", "title": "debt_name"},
    "Hisabi Jameya": {"name": "jameya_name", "title": "jameya_name"},
    "Hisabi Transaction": {
        "type": "transaction_type",
        "account_id": "account",
        "accountId": "account",
        "to_account_id": "to_account",
        "toAccountId": "to_account",
        "category_id": "category",
        "categoryId": "category",
        "bucket_id": "bucket",
        "bucketId": "bucket",
        "amountBase": "amount_base",
        "base_amount": "amount_base",
        "fxRateUsed": "fx_rate_used",
        "fx_rate": "fx_rate_used",
        "originalAmount": "original_amount",
        "originalCurrency": "original_currency",
        "convertedAmount": "converted_amount",
    },
}

# Sync identity invariant: these doctypes must keep primary key == client_id.
# This avoids client/server drift when links and queue items are keyed by client_id.
SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES = {
    "Hisabi Account",
    "Hisabi Category",
    "Hisabi Bucket",
    "Hisabi Bucket Template",
    "Hisabi Transaction Bucket",
    "Hisabi Transaction Bucket Expense",
    "Hisabi Recurring Rule",
    "Hisabi Recurring Instance",
}

SYNC_PUSH_DATETIME_FIELDS = {
    "Hisabi Wallet Member": {"joined_at", "removed_at"},
    "Hisabi Settings": {"deleted_at"},
    "Hisabi FX Rate": {"effective_date", "last_updated", "deleted_at"},
    "Hisabi Custom Currency": {"deleted_at"},
    "Hisabi Transaction": {"date_time", "deleted_at"},
    "Hisabi Debt": {"due_date", "deleted_at"},
    "Hisabi Debt Installment": {"due_date", "paid_at", "deleted_at"},
    "Hisabi Budget": {"start_date", "end_date", "deleted_at"},
    "Hisabi Goal": {"target_date", "deleted_at"},
    "Hisabi Jameya": {"start_date", "deleted_at"},
    "Hisabi Jameya Payment": {"due_date", "paid_at", "deleted_at"},
    "Hisabi Transaction Bucket": {"deleted_at"},
    "Hisabi Transaction Bucket Expense": {"deleted_at"},
    "Hisabi Bucket Template": {"deleted_at"},
    "Hisabi Attachment": {"deleted_at"},
    "Hisabi Recurring Rule": {"start_date", "until_date", "resume_date", "last_generated_at", "deleted_at"},
    "Hisabi Recurring Instance": {"occurrence_date", "generated_at", "deleted_at"},
}


def _require_device_auth(device_id: str) -> Tuple[str, frappe.model.document.Document]:
    return require_device_auth(device_id)


def _get_doc_by_client_id(
    doctype: str, user: str, client_id: str, *, wallet_id: str | None = None
) -> Optional[frappe.model.document.Document]:
    meta = frappe.get_meta(doctype)
    filters: Dict[str, Any] = {"client_id": client_id}
    # Shared wallet: authoritative scoping is wallet_id, not user.
    if meta.has_field("wallet_id") and wallet_id:
        filters["wallet_id"] = wallet_id
    elif meta.has_field("user"):
        filters["user"] = user
    else:
        filters["owner"] = user

    name = frappe.get_value(doctype, filters)
    if not name:
        return None
    return frappe.get_doc(doctype, name)


def _set_owner(doc: frappe.model.document.Document, user: str) -> None:
    # Keep creator/owner stable once created.
    if doc.is_new():
        if doc.meta.has_field("user"):
            doc.user = user
        else:
            doc.owner = user


def _strip_server_auth_fields(doctype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    payload = dict(payload)
    for field in SERVER_AUTH_FIELDS.get(doctype, set()):
        payload.pop(field, None)
    return payload


def _apply_field_map(doctype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    payload = dict(payload)
    for old, new in FIELD_MAP.get(doctype, {}).items():
        if old in payload and new not in payload:
            payload[new] = payload.pop(old)
    return payload


def _normalize_sync_datetime_fields(doctype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    datetime_fields = SYNC_PUSH_DATETIME_FIELDS.get(doctype, set())
    if not datetime_fields:
        return payload
    normalized = dict(payload)
    for field in datetime_fields:
        value = normalized.get(field)
        if value in (None, ""):
            continue
        parsed = get_datetime(value)
        if not parsed:
            continue
        # Data correctness: normalize ISO-8601 values (including `Z`) before DB datetime writes.
        normalized[field] = _cursor_dt(parsed) or parsed
    return normalized


def _normalize_json_field_values(
    doc: frappe.model.document.Document, payload: Dict[str, Any]
) -> Dict[str, Any]:
    if not payload:
        return {}
    normalized = dict(payload)
    for fieldname, value in list(normalized.items()):
        if value is None:
            continue
        df = doc.meta.get_field(fieldname)
        if not df or df.fieldtype != "JSON":
            continue
        if isinstance(value, (list, dict)):
            normalized[fieldname] = frappe.as_json(value)
    return normalized


def _normalize_bucket_template_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    normalized = dict(payload)
    rows = normalized.get("template_items")
    if not isinstance(rows, list):
        return normalized

    normalized_rows: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bucket_id = (row.get("bucket_id") or row.get("bucket") or row.get("bucketId") or "").strip()
        percentage = row.get("percentage")
        if percentage in (None, ""):
            percentage = row.get("percent")
        normalized_rows.append(
            {
                "bucket_id": bucket_id,
                "percentage": percentage,
            }
        )
    normalized["template_items"] = normalized_rows
    return normalized


def _sync_transaction_bucket_mirror(
    source_doc: frappe.model.document.Document,
    source_doctype: str,
    *,
    operation: str,
) -> None:
    if source_doctype not in {"Hisabi Transaction Allocation", "Hisabi Transaction Bucket"}:
        return

    target_doctype = (
        "Hisabi Transaction Bucket"
        if source_doctype == "Hisabi Transaction Allocation"
        else "Hisabi Transaction Allocation"
    )
    if not frappe.db.exists("DocType", target_doctype):
        return

    source_user = getattr(source_doc, "user", None) or frappe.session.user
    source_wallet_id = getattr(source_doc, "wallet_id", None)
    source_client_id = getattr(source_doc, "client_id", None) or source_doc.name
    if not source_wallet_id or not source_client_id:
        return

    transaction_id = getattr(source_doc, "transaction_id", None) or getattr(source_doc, "transaction", None)
    bucket_id = getattr(source_doc, "bucket_id", None) or getattr(source_doc, "bucket", None)
    amount = getattr(source_doc, "amount", None)
    percentage = getattr(source_doc, "percentage", None)
    if percentage in (None, ""):
        percentage = getattr(source_doc, "percent", None)
    currency = getattr(source_doc, "currency", None)
    amount_base = getattr(source_doc, "amount_base", None)
    rule_used = getattr(source_doc, "rule_used", None)
    is_manual_override = getattr(source_doc, "is_manual_override", None)
    if is_manual_override in (None, ""):
        is_manual_override = 1 if str(source_client_id).endswith(":manual") else 0

    if not currency and transaction_id:
        currency = frappe.get_value("Hisabi Transaction", transaction_id, "currency")
    if amount_base in (None, "") and amount not in (None, ""):
        amount_base = flt(amount, 2)

    mark_deleted = cint(getattr(source_doc, "is_deleted", 0) or 0) == 1 or operation == "delete"

    target_doc = _get_doc_by_client_id(
        target_doctype,
        source_user,
        source_client_id,
        wallet_id=source_wallet_id,
    )
    if not target_doc:
        if not transaction_id or not bucket_id:
            return
        target_doc = frappe.new_doc(target_doctype)
        _set_owner(target_doc, source_user)
        target_doc.client_id = source_client_id
        target_doc.name = source_client_id
        if target_doctype in SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES:
            target_doc.flags.name_set = True

    target_doc.wallet_id = source_wallet_id
    if target_doc.meta.has_field("user"):
        target_doc.user = source_user

    if target_doctype == "Hisabi Transaction Allocation":
        if transaction_id:
            target_doc.transaction = transaction_id
        if bucket_id:
            target_doc.bucket = bucket_id
        if amount not in (None, ""):
            target_doc.amount = flt(amount, 2)
        if percentage not in (None, ""):
            target_doc.percent = flt(percentage, 6)
        if currency:
            target_doc.currency = currency
        if amount_base not in (None, ""):
            target_doc.amount_base = flt(amount_base, 2)
        if target_doc.meta.has_field("rule_used"):
            target_doc.rule_used = rule_used
        if target_doc.meta.has_field("is_manual_override"):
            target_doc.is_manual_override = cint(is_manual_override)
    else:
        if transaction_id:
            target_doc.transaction_id = transaction_id
        if bucket_id:
            target_doc.bucket_id = bucket_id
        if amount not in (None, ""):
            target_doc.amount = flt(amount, 2)
        if percentage not in (None, ""):
            target_doc.percentage = flt(percentage, 6)

    apply_common_sync_fields(target_doc, bump_version=True, mark_deleted=mark_deleted)
    if mark_deleted and target_doc.meta.has_field("deleted_at") and not target_doc.deleted_at:
        target_doc.deleted_at = now_datetime()
    target_doc.save(ignore_permissions=True)


def _resolve_base_currency(wallet_id: str, user: str) -> str | None:
    currency = frappe.get_value(
        "Hisabi Settings",
        {"wallet_id": wallet_id, "is_deleted": 0},
        "base_currency",
    )
    if not currency:
        currency = frappe.get_value(
            "Hisabi Settings",
            {"user": user, "is_deleted": 0},
            "base_currency",
        )
    if not currency:
        currency = frappe.db.get_single_value("System Settings", "currency")
    return currency


def _normalize_group_id(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized


def _normalize_account_currency(value: Any) -> str:
    return _normalize_currency_code(value or "")


def _resolve_account_doc(
    account_ref: Any,
    *,
    user: str,
    wallet_id: str,
) -> Optional[frappe.model.document.Document]:
    if not account_ref:
        return None
    ref = str(account_ref).strip()
    if not ref:
        return None

    if frappe.db.exists("Hisabi Account", ref):
        doc = frappe.get_doc("Hisabi Account", ref)
        if doc.wallet_id == wallet_id:
            return doc

    return _get_doc_by_client_id("Hisabi Account", user, ref, wallet_id=wallet_id)


def _is_multi_currency_parent(account_doc: frappe.model.document.Document | None) -> bool:
    if not account_doc:
        return False
    return (
        cint(getattr(account_doc, "is_multi_currency", 0) or 0) == 1
        and not getattr(account_doc, "parent_account", None)
        and cint(getattr(account_doc, "is_deleted", 0) or 0) == 0
    )


def _find_multi_currency_child(
    *,
    wallet_id: str,
    group_id: str,
    currency: str,
) -> Optional[frappe.model.document.Document]:
    if not wallet_id or not group_id or not currency:
        return None
    names = frappe.get_all(
        "Hisabi Account",
        filters={
            "wallet_id": wallet_id,
            "group_id": group_id,
            "currency": currency,
            "is_deleted": 0,
            "parent_account": ["is", "set"],
        },
        pluck="name",
        limit=1,
        order_by="creation asc",
    )
    if not names:
        return None
    return frappe.get_doc("Hisabi Account", names[0])


def _build_child_client_id(parent_doc: frappe.model.document.Document, currency: str) -> str:
    parent_client_id = str(getattr(parent_doc, "client_id", "") or getattr(parent_doc, "name", "")).strip()
    normalized_currency = str(currency or "").strip().lower()
    if not parent_client_id:
        parent_client_id = f"acc-{uuid.uuid4().hex[:12]}"
    if not normalized_currency:
        normalized_currency = "cur"
    return f"{parent_client_id}-{normalized_currency}"


def _ensure_multi_currency_child(
    parent_doc: frappe.model.document.Document,
    *,
    currency: str,
    user: str,
) -> frappe.model.document.Document:
    normalized_currency = _normalize_account_currency(currency)
    if not normalized_currency:
        normalized_currency = _normalize_account_currency(parent_doc.base_currency or parent_doc.currency) or "USD"

    group_id = _normalize_group_id(parent_doc.group_id) or str(parent_doc.client_id or parent_doc.name)
    if not parent_doc.group_id:
        parent_doc.group_id = group_id
        parent_doc.db_set("group_id", group_id, update_modified=False)

    existing = _find_multi_currency_child(
        wallet_id=parent_doc.wallet_id,
        group_id=group_id,
        currency=normalized_currency,
    )
    if existing:
        return existing

    child_client_id = _build_child_client_id(parent_doc, normalized_currency)
    child = _get_doc_by_client_id("Hisabi Account", user, child_client_id, wallet_id=parent_doc.wallet_id)
    if child:
        return child

    child = frappe.new_doc("Hisabi Account")
    _set_owner(child, user)
    child.client_id = child_client_id
    child.name = child_client_id
    if "Hisabi Account" in SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES:
        child.flags.name_set = True

    child.wallet_id = parent_doc.wallet_id
    child.account_name = f"{parent_doc.account_name} ({normalized_currency})"
    child.account_type = parent_doc.account_type
    child.currency = normalized_currency
    child.base_currency = _normalize_account_currency(parent_doc.base_currency or parent_doc.currency)
    child.group_id = group_id
    child.parent_account = parent_doc.name
    child.is_multi_currency = 0
    child.opening_balance = 0
    child.current_balance = 0
    child.color = parent_doc.color
    child.icon = parent_doc.icon
    child.archived = parent_doc.archived
    apply_common_sync_fields(child, bump_version=True, mark_deleted=False)
    child.save(ignore_permissions=True)
    return child


def _resolve_fx_rate_for_wallet(
    *,
    wallet_id: str,
    source_currency: str,
    target_currency: str,
    at: Any = None,
) -> Optional[float]:
    source = _normalize_account_currency(source_currency)
    target = _normalize_account_currency(target_currency)
    if not source or not target:
        return None
    if source == target:
        return 1.0

    effective_date = get_datetime(at) or now_datetime()
    direct = frappe.db.sql(
        """
        SELECT rate
        FROM `tabHisabi FX Rate`
        WHERE wallet_id=%s
          AND is_deleted=0
          AND base_currency=%s
          AND quote_currency=%s
          AND DATE(effective_date) <= %s
        ORDER BY effective_date DESC, server_modified DESC, name DESC
        LIMIT 1
        """,
        (wallet_id, source, target, effective_date.date()),
        as_dict=True,
    )
    if direct:
        rate = flt(direct[0].get("rate") or 0)
        if rate > 0:
            return rate

    inverse = frappe.db.sql(
        """
        SELECT rate
        FROM `tabHisabi FX Rate`
        WHERE wallet_id=%s
          AND is_deleted=0
          AND base_currency=%s
          AND quote_currency=%s
          AND DATE(effective_date) <= %s
        ORDER BY effective_date DESC, server_modified DESC, name DESC
        LIMIT 1
        """,
        (wallet_id, target, source, effective_date.date()),
        as_dict=True,
    )
    if inverse:
        reverse_rate = flt(inverse[0].get("rate") or 0)
        if reverse_rate > 0:
            return 1.0 / reverse_rate
    return None


def _convert_amount_between_currencies(
    *,
    wallet_id: str,
    amount: float,
    source_currency: str,
    target_currency: str,
    at: Any = None,
) -> Tuple[Optional[float], Optional[float]]:
    source = _normalize_account_currency(source_currency)
    target = _normalize_account_currency(target_currency)
    numeric_amount = flt(amount or 0)
    if not source or not target:
        return None, None
    if source == target:
        return numeric_amount, 1.0

    rate = _resolve_fx_rate_for_wallet(
        wallet_id=wallet_id,
        source_currency=source,
        target_currency=target,
        at=at,
    )
    if not rate or rate <= 0:
        return None, None
    return flt(numeric_amount * rate, 8), flt(rate, 8)


def _hydrate_transaction_fx_fields(
    payload: Dict[str, Any],
    *,
    user: str,
    wallet_id: str,
) -> Dict[str, Any]:
    if not payload:
        return payload
    normalized = dict(payload)
    normalized["currency"] = _normalize_account_currency(normalized.get("currency"))

    account_doc = _resolve_account_doc(normalized.get("account"), user=user, wallet_id=wallet_id)
    if not account_doc:
        return normalized

    tx_currency = _normalize_account_currency(normalized.get("currency")) or _normalize_account_currency(account_doc.currency)
    account_currency = _normalize_account_currency(account_doc.currency)
    normalized["currency"] = tx_currency

    amount = flt(normalized.get("amount") or 0)
    if amount <= 0:
        return normalized

    fx_rate_used = flt(normalized.get("fx_rate_used") or normalized.get("fx_rate") or 0)
    if tx_currency != account_currency and fx_rate_used <= 0:
        inferred = _resolve_fx_rate_for_wallet(
            wallet_id=wallet_id,
            source_currency=tx_currency,
            target_currency=account_currency,
            at=normalized.get("date_time"),
        )
        if inferred and inferred > 0:
            fx_rate_used = inferred
    if tx_currency == account_currency and fx_rate_used <= 0:
        fx_rate_used = 1.0

    if fx_rate_used > 0:
        normalized["fx_rate_used"] = flt(fx_rate_used, 8)
        if tx_currency != account_currency and normalized.get("converted_amount") in (None, ""):
            normalized["converted_amount"] = flt(amount * fx_rate_used, 8)

    if normalized.get("amount_base") in (None, "") and normalized.get("base_amount") not in (None, ""):
        normalized["amount_base"] = normalized.get("base_amount")
    if normalized.get("amount_base") not in (None, ""):
        return normalized

    amount_in_account_currency = amount
    if tx_currency != account_currency:
        converted = flt(normalized.get("converted_amount") or 0)
        if converted > 0:
            amount_in_account_currency = converted
        elif fx_rate_used > 0:
            amount_in_account_currency = flt(amount * fx_rate_used, 8)

    base_currency = _normalize_account_currency(_resolve_base_currency(wallet_id, user) or account_currency)
    converted_base, _ = _convert_amount_between_currencies(
        wallet_id=wallet_id,
        amount=amount_in_account_currency,
        source_currency=account_currency,
        target_currency=base_currency,
        at=normalized.get("date_time"),
    )
    if converted_base is not None:
        normalized["amount_base"] = flt(converted_base, 8)
    elif account_currency == base_currency:
        normalized["amount_base"] = flt(amount_in_account_currency, 8)
    return normalized


def _resolve_multi_currency_transaction_accounts(
    payload: Dict[str, Any],
    *,
    user: str,
    wallet_id: str,
) -> Tuple[Dict[str, Any], set[str]]:
    if not payload:
        return payload, set()
    normalized = dict(payload)
    affected_parents: set[str] = set()
    tx_currency = _normalize_account_currency(normalized.get("currency"))

    for fieldname in ("account", "to_account"):
        account_doc = _resolve_account_doc(normalized.get(fieldname), user=user, wallet_id=wallet_id)
        if not _is_multi_currency_parent(account_doc):
            continue

        desired_currency = tx_currency
        if fieldname == "to_account":
            desired_currency = _normalize_account_currency(account_doc.base_currency or account_doc.currency or tx_currency)
        if not desired_currency:
            desired_currency = _normalize_account_currency(account_doc.base_currency or account_doc.currency)

        child = _ensure_multi_currency_child(account_doc, currency=desired_currency, user=user)
        normalized[fieldname] = child.name
        affected_parents.add(account_doc.name)

    return normalized, affected_parents


def _create_base_child_for_multi_currency_parent(
    account_doc: frappe.model.document.Document,
    *,
    user: str,
) -> Optional[frappe.model.document.Document]:
    if not _is_multi_currency_parent(account_doc):
        return None
    base_currency = _normalize_account_currency(account_doc.base_currency or account_doc.currency)
    if not base_currency:
        return None
    return _ensure_multi_currency_child(account_doc, currency=base_currency, user=user)


def _recalculate_multi_currency_parent_balance(
    *,
    user: str,
    wallet_id: str,
    parent_account_id: str,
) -> None:
    parent_doc = _resolve_account_doc(parent_account_id, user=user, wallet_id=wallet_id)
    if not _is_multi_currency_parent(parent_doc):
        return

    base_currency = _normalize_account_currency(parent_doc.base_currency or parent_doc.currency)
    child_rows = frappe.get_all(
        "Hisabi Account",
        filters={
            "wallet_id": wallet_id,
            "parent_account": parent_doc.name,
            "is_deleted": 0,
        },
        fields=["name", "currency", "current_balance"],
    )
    total_balance = 0.0
    for row in child_rows:
        child_currency = _normalize_account_currency(row.get("currency"))
        child_balance = flt(row.get("current_balance") or 0)
        converted, _ = _convert_amount_between_currencies(
            wallet_id=wallet_id,
            amount=child_balance,
            source_currency=child_currency,
            target_currency=base_currency,
            at=now_datetime(),
        )
        if converted is None:
            if child_currency != base_currency:
                continue
            converted = child_balance
        total_balance += flt(converted)

    if abs(flt(parent_doc.current_balance or 0) - flt(total_balance)) <= 0.000001:
        return
    parent_doc.current_balance = flt(total_balance, 8)
    apply_common_sync_fields(parent_doc, bump_version=True, mark_deleted=False)
    parent_doc.save(ignore_permissions=True)


def _enrich_multi_currency_account_payload(
    payload: Dict[str, Any],
    *,
    user: str,
    wallet_id: str,
) -> Dict[str, Any]:
    if not payload or cint(payload.get("is_multi_currency") or 0) != 1:
        return payload

    parent_doc = _resolve_account_doc(payload.get("client_id") or payload.get("name"), user=user, wallet_id=wallet_id)
    if not parent_doc:
        return payload

    base_currency = _normalize_account_currency(parent_doc.base_currency or parent_doc.currency)
    rows = frappe.get_all(
        "Hisabi Account",
        filters={
            "wallet_id": wallet_id,
            "parent_account": parent_doc.name,
            "is_deleted": 0,
        },
        fields=["name", "client_id", "currency", "current_balance", "opening_balance"],
        order_by="creation asc",
    )

    supported_currencies: List[str] = []
    sub_balances: List[Dict[str, Any]] = []
    total_balance_base = 0.0
    for row in rows:
        sub_currency = _normalize_account_currency(row.get("currency"))
        sub_balance = flt(row.get("current_balance") or 0)
        supported_currencies.append(sub_currency)
        sub_balances.append(
            {
                "account_id": row.get("client_id") or row.get("name"),
                "currency": sub_currency,
                "balance": flt(sub_balance, 8),
                "opening_balance": flt(row.get("opening_balance") or 0, 8),
            }
        )
        converted, _ = _convert_amount_between_currencies(
            wallet_id=wallet_id,
            amount=sub_balance,
            source_currency=sub_currency,
            target_currency=base_currency,
            at=now_datetime(),
        )
        if converted is None:
            if sub_currency != base_currency:
                continue
            converted = sub_balance
        total_balance_base += flt(converted)

    payload["base_currency"] = base_currency
    payload["group_id"] = _normalize_group_id(parent_doc.group_id) or str(parent_doc.client_id or parent_doc.name)
    payload["supported_currencies"] = sorted({c for c in supported_currencies if c})
    payload["sub_balances"] = sub_balances
    payload["total_balance_base"] = flt(total_balance_base, 8)
    return payload


def _filter_payload_fields(doc: frappe.model.document.Document, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    allowed = {field.fieldname for field in doc.meta.fields}
    return {key: value for key, value in payload.items() if key in allowed}


def _unknown_payload_fields(doctype: str, payload: Dict[str, Any]) -> List[str]:
    if not payload:
        return []
    allowed = SYNC_PUSH_ALLOWED_FIELDS.get(doctype, set())
    if not allowed:
        return []
    return sorted(
        [key for key in payload.keys() if key not in allowed and key not in SYNC_PAYLOAD_LOG_IGNORE_KEYS]
    )


def _store_op_id(
    *,
    user: str,
    device_id: str,
    wallet_id: str,
    op_id: str,
    entity_type: str,
    entity_client_id: str,
    status: str,
    payload: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    if not device_id or not op_id:
        return

    ledger_op_id = _ledger_op_id(wallet_id, op_id)
    if frappe.db.exists("Hisabi Sync Op", {"user": user, "device_id": device_id, "op_id": ledger_op_id}):
        return

    sync_op = frappe.new_doc("Hisabi Sync Op")
    sync_op.user = user
    sync_op.device_id = device_id
    sync_op.op_id = ledger_op_id
    sync_op.entity_type = entity_type
    sync_op.client_id = entity_client_id
    sync_op.status = status
    sync_op.result_json = json.dumps(result, ensure_ascii=False)
    if result.get("server_modified"):
        sync_op.server_modified = result.get("server_modified")
    sync_op.save(ignore_permissions=True)


def _ledger_op_id(wallet_id: str, op_id: str) -> str:
    # Reliability: retries must be safe; op_id provides idempotency.
    return f"{wallet_id}:{op_id}"


def _check_duplicate_op(user: str, device_id: str, wallet_id: str, op_id: str) -> bool:
    if not device_id or not op_id:
        return False
    return bool(
        frappe.db.exists(
            "Hisabi Sync Op",
            {"user": user, "device_id": device_id, "op_id": _ledger_op_id(wallet_id, op_id)},
        )
    )


def _get_op_status(user: str, device_id: str, wallet_id: str, op_id: str) -> Optional[str]:
    if not device_id or not op_id:
        return None
    return frappe.get_value(
        "Hisabi Sync Op",
        {"user": user, "device_id": device_id, "op_id": _ledger_op_id(wallet_id, op_id)},
        "status",
    )


def _recalculate_account_balance(user: str, account_name: str, *, wallet_id: str | None = None) -> None:
    recalc_account_balance(user, account_name, wallet_id=wallet_id)


def _ensure_supported_doctype(doctype: str) -> None:
    if doctype not in DOCTYPE_LIST:
        frappe.throw(_("Unsupported entity_type: {0}").format(doctype), frappe.ValidationError)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(_("DocType not installed: {0}").format(doctype), frappe.ValidationError)
    frappe.get_meta(doctype)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _invalid_field_types(doctype: str, payload: Dict[str, Any], fields: set[str]) -> Dict[str, str]:
    invalid: Dict[str, str] = {}
    datetime_fields = SYNC_PUSH_DATETIME_FIELDS.get(doctype, set())
    for field in fields:
        expected = SYNC_PUSH_FIELD_TYPES.get(field)
        if not expected or field not in payload:
            continue
        value = payload.get(field)
        if expected == "string":
            if isinstance(value, str):
                continue
            if field in datetime_fields and isinstance(value, (datetime.datetime, datetime.date)):
                continue
            invalid[field] = "string"
        elif expected == "number" and not _is_number(value):
            invalid[field] = "number"
        elif expected == "list" and not isinstance(value, list):
            invalid[field] = "list"
        elif expected == "json" and not isinstance(value, (list, dict)):
            invalid[field] = "json"
    return invalid


def _normalize_currency_code(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _collect_transaction_fx_sanity_warnings(doc: frappe.model.document.Document) -> List[str]:
    if getattr(doc, "doctype", "") != "Hisabi Transaction":
        return []
    account = getattr(doc, "account", None)
    tx_currency = _normalize_currency_code(getattr(doc, "currency", None))
    if not account or not tx_currency:
        return []

    account_currency = _normalize_currency_code(frappe.get_value("Hisabi Account", account, "currency"))
    if not account_currency or account_currency == tx_currency:
        return []

    warnings: List[str] = []
    fx_rate_used = flt(getattr(doc, "fx_rate_used", 0) or 0)
    if fx_rate_used <= 0:
        warnings.append("fx_rate_non_positive_for_currency_mismatch")

    amount = flt(getattr(doc, "amount", 0) or 0)
    converted_amount = flt(getattr(doc, "converted_amount", 0) or 0)
    if amount > 0 and converted_amount <= 0:
        warnings.append("converted_amount_missing_for_currency_mismatch")

    if warnings:
        frappe.logger("hisabi_backend.sync").warning(
            "fx_sanity_warning",
            extra={
                "transaction": getattr(doc, "client_id", None) or getattr(doc, "name", None),
                "wallet_id": getattr(doc, "wallet_id", None),
                "currency": tx_currency,
                "account_currency": account_currency,
                "warnings": warnings,
            },
        )
    return warnings


ERROR_MESSAGE_MAP = {
    "entity_type_required": "entity_type is required",
    "op_id_required": "op_id is required",
    "unsupported_entity_type": "unsupported_entity_type",
    "doctype_not_installed": "doctype_not_installed",
    "invalid_operation": "invalid operation",
    "entity_id_required": "entity_id is required",
    "payload_must_be_object": "payload must be an object",
    "wallet_id_mismatch": "عدم تطابق معرف المحفظة (wallet_id_mismatch)",
    "entity_id_mismatch": "entity_id does not match payload client_id",
    "invalid_client_id": "invalid client_id",
    "base_version_required": "base_version is required",
    "base_version_invalid": "base_version must be a number",
    "base_version_not_allowed": "base_version must be absent for create",
    "missing_required_fields": "missing required fields",
    "invalid_field": "invalid field",
    "invalid_field_type": "invalid field type",
    "sensitive_field_not_allowed": "sensitive field is not allowed in sync payload",
    "not_found": "record not found",
    "payload_too_large": "payload too large",
    "wallet_id_must_equal_client_id": "wallet_id must equal client_id",
    "wallet_access_denied": "wallet access denied",
    "rejected": "request rejected",
}


def _build_item_error(
    *,
    error_code: str,
    entity_type: Optional[str] = None,
    client_id: Optional[str] = None,
    detail: Any = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "error",
        "error": error_code,
        "error_code": error_code,
        "error_message": ERROR_MESSAGE_MAP.get(error_code, error_code),
    }
    if entity_type:
        payload["entity_type"] = entity_type
    if client_id:
        payload["client_id"] = client_id
    if detail is not None:
        payload["detail"] = detail
    return payload


def _build_item_rejected(
    *,
    op_id: Optional[str],
    entity_type: Optional[str],
    client_id: Optional[str],
    detail: Any = None,
) -> Dict[str, Any]:
    payload = _build_item_error(
        error_code="rejected",
        entity_type=entity_type,
        client_id=client_id,
        detail=detail,
    )
    payload["status"] = "rejected"
    if op_id:
        payload["op_id"] = op_id
    return payload


def _build_sync_response(payload: Dict[str, Any], status_code: int = 200) -> Response:
    response = Response()
    response.mimetype = "application/json"
    response.status_code = status_code
    response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return response


def _build_sync_error(error_code: str, message: str, *, status_code: int = 417) -> Response:
    return _build_sync_response({"error": error_code, "message": message}, status_code=status_code)


def _sync_status_for_exception(exc: Exception) -> int:
    if isinstance(exc, frappe.AuthenticationError):
        return 401
    if isinstance(exc, frappe.PermissionError):
        return 403
    if isinstance(exc, frappe.ValidationError):
        return 417
    return 500


def _validate_sync_push_item(item: Dict[str, Any], wallet_id: str) -> Optional[Dict[str, Any]]:
    op_id = item.get("op_id")
    if not isinstance(op_id, str) or not op_id.strip():
        return _build_item_error(error_code="op_id_required")

    entity_type = item.get("entity_type")
    if not entity_type:
        return _build_item_error(error_code="entity_type_required")

    if entity_type not in SYNC_PUSH_ALLOWLIST:
        return _build_item_error(error_code="unsupported_entity_type", entity_type=entity_type)

    if not frappe.db.exists("DocType", entity_type):
        return _build_item_error(error_code="doctype_not_installed", entity_type=entity_type)

    operation = item.get("operation")
    if operation not in {"create", "update", "delete"}:
        return _build_item_error(error_code="invalid_operation", entity_type=entity_type)

    entity_id = item.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        return _build_item_error(error_code="entity_id_required", entity_type=entity_type)

    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        return _build_item_error(error_code="payload_must_be_object", entity_type=entity_type)

    lowered_payload_keys = {str(key).strip().lower() for key in payload.keys()}
    blocked_sensitive_fields = sorted([field for field in SENSITIVE_SYNC_FIELDS if field in lowered_payload_keys])
    if blocked_sensitive_fields:
        return _build_item_error(
            error_code="sensitive_field_not_allowed",
            entity_type=entity_type,
            detail=blocked_sensitive_fields,
        )

    if payload.get("wallet_id") and payload.get("wallet_id") != wallet_id:
        return _build_item_error(error_code="wallet_id_mismatch", entity_type=entity_type)

    try:
        ensure_entity_id_matches(entity_id, payload.get("client_id"))
    except Exception:
        return _build_item_error(error_code="entity_id_mismatch", entity_type=entity_type)

    client_id = payload.get("client_id") or entity_id
    try:
        validate_client_id(client_id)
    except Exception:
        return _build_item_error(error_code="invalid_client_id", entity_type=entity_type, client_id=client_id)

    base_version = item.get("base_version")
    if operation in {"update", "delete"}:
        if base_version is None:
            return _build_item_error(
                error_code="base_version_required",
                entity_type=entity_type,
                client_id=client_id,
            )
        if not _is_number(base_version):
            return _build_item_error(
                error_code="base_version_invalid",
                entity_type=entity_type,
                client_id=client_id,
            )
    if operation == "create" and base_version is not None:
        return _build_item_error(
            error_code="base_version_not_allowed",
            entity_type=entity_type,
            client_id=client_id,
        )

    normalized = _apply_field_map(entity_type, payload)
    unknown_fields = _unknown_payload_fields(entity_type, normalized)
    if unknown_fields:
        return _build_item_error(
            error_code="invalid_field",
            entity_type=entity_type,
            client_id=client_id,
            detail=unknown_fields,
        )

    strict_optional_fields = {"phone_number", "notifications_preferences", "enforce_fx"}
    typed_fields = {field for field in normalized.keys() if field in strict_optional_fields}
    invalid_types = _invalid_field_types(entity_type, normalized, typed_fields)
    if invalid_types:
        return _build_item_error(
            error_code="invalid_field_type",
            entity_type=entity_type,
            client_id=client_id,
            detail=invalid_types,
        )

    if operation == "create":
        required = SYNC_PUSH_REQUIRED_FIELDS_CREATE.get(entity_type, set())
        missing = [field for field in required if normalized.get(field) in (None, "")]
        if missing:
            return _build_item_error(
                error_code="missing_required_fields",
                entity_type=entity_type,
                client_id=client_id,
                detail=missing,
            )

        for group in SYNC_PUSH_REQUIRED_FIELD_GROUPS.get(entity_type, []):
            if not any(normalized.get(field) not in (None, "") for field in group):
                return _build_item_error(
                    error_code="missing_required_fields",
                    entity_type=entity_type,
                    client_id=client_id,
                    detail=sorted(group),
                )

        required_typed_fields = {field for field in required if SYNC_PUSH_FIELD_TYPES.get(field)}
        invalid_types = _invalid_field_types(entity_type, normalized, required_typed_fields)
        if invalid_types:
            return _build_item_error(
                error_code="invalid_field_type",
                entity_type=entity_type,
                client_id=client_id,
                detail=invalid_types,
            )

        if entity_type == "Hisabi Account":
            is_multi_currency = cint(normalized.get("is_multi_currency") or 0) == 1
            missing_account_fields = []
            if is_multi_currency:
                if normalized.get("base_currency") in (None, ""):
                    missing_account_fields.append("base_currency")
            elif normalized.get("currency") in (None, ""):
                missing_account_fields.append("currency")
            if missing_account_fields:
                return _build_item_error(
                    error_code="missing_required_fields",
                    entity_type=entity_type,
                    client_id=client_id,
                    detail=missing_account_fields,
                )
            if is_multi_currency and normalized.get("parent_account") not in (None, ""):
                return _build_item_error(
                    error_code="invalid_field",
                    entity_type=entity_type,
                    client_id=client_id,
                    detail=["parent_account"],
                )

    return None


def _prepare_doc_for_write(
    doctype: str,
    payload: Dict[str, Any],
    user: str,
    *,
    existing: Optional[frappe.model.document.Document],
) -> frappe.model.document.Document:
    doc = existing or frappe.new_doc(doctype)
    _set_owner(doc, user)

    client_id = payload.get("client_id")
    if not client_id:
        client_id = payload.get("entity_id")
    validate_client_id(client_id)
    doc.client_id = client_id

    if not existing:
        doc.name = client_id
        if doctype in SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES:
            doc.flags.name_set = True
    elif doc.name != client_id and doctype in SYNC_CLIENT_ID_PRIMARY_KEY_DOCTYPES:
        # Keep client identity stable for sync-safe links and deterministic retries.
        doc = _rename_doc_to_client_id(doc, client_id)

    payload = _apply_field_map(doctype, payload)
    payload = _strip_server_auth_fields(doctype, payload)
    if doctype == "Hisabi Bucket Template":
        payload = _normalize_bucket_template_payload(payload)
    payload = _filter_payload_fields(doc, payload)
    payload = _normalize_sync_datetime_fields(doctype, payload)
    payload = _normalize_json_field_values(doc, payload)
    ensure_link_ownership(
        doctype,
        payload,
        user,
        wallet_id=payload.get("wallet_id") or getattr(doc, "wallet_id", None),
    )
    doc.update(payload)

    return doc


def _rename_doc_to_client_id(
    doc: frappe.model.document.Document, client_id: str
) -> frappe.model.document.Document:
    if doc.name == client_id:
        return doc
    frappe.rename_doc(
        doc.doctype,
        doc.name,
        client_id,
        force=True,
        ignore_permissions=True,
    )
    return frappe.get_doc(doc.doctype, client_id)


def _to_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    dt = get_datetime(value)
    return dt.isoformat() if dt else None


def _cursor_dt(dt: Any) -> Optional[datetime.datetime]:
    """Cursor safety: normalize datetime to UTC-naive for stable tuple comparisons.
    Fixes aware vs naive TypeError in sync_pull.
    """
    if dt is None:
        return None
    parsed = dt if isinstance(dt, datetime.datetime) else get_datetime(dt)
    if not parsed:
        return None
    # Normalize both cursor/input timestamps to a single representation before tuple compare.
    if parsed.tzinfo is not None:
        return parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _minimal_server_record(doc: frappe.model.document.Document) -> Dict[str, Any]:
    stable_name = doc.client_id or doc.name
    return {
        "name": stable_name,
        "client_id": doc.client_id,
        "doc_version": doc.doc_version,
        "server_modified": _to_iso(doc.server_modified),
        "is_deleted": doc.is_deleted,
        "deleted_at": _to_iso(doc.deleted_at),
    }


def _sanitize_pull_record(doctype: str, record: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(record or {})
    if doctype == "Hisabi Device":
        cleaned.pop("device_token_hash", None)
    for sensitive_key in SENSITIVE_SYNC_FIELDS:
        cleaned.pop(sensitive_key, None)
        cleaned.pop(sensitive_key.lower(), None)
        cleaned.pop(sensitive_key.upper(), None)

    allowed = SYNC_PULL_ALLOWED_FIELDS.get(doctype)
    if not allowed:
        # Keep unknown doctypes backward-compatible while dropping noisy framework fields.
        return {key: value for key, value in cleaned.items() if key not in SYNC_PULL_SYSTEM_FIELDS}

    sanitized = {key: cleaned[key] for key in allowed if key in cleaned}
    # Contract stability: include both `client_id` and `name` consistently.
    # For syncable docs, expose `name == client_id` even if legacy DB rows had a different name.
    stable_client_id = sanitized.get("client_id") or cleaned.get("client_id")
    if stable_client_id:
        sanitized["client_id"] = stable_client_id
        sanitized["name"] = stable_client_id
    elif cleaned.get("name"):
        # Backward compatibility for doctypes without client_id (e.g. wallet members).
        sanitized["name"] = cleaned.get("name")
    return sanitized


def _conflict_response(
    doctype: str,
    doc: frappe.model.document.Document,
    *,
    op_id: str,
    client_base_version: int | None,
) -> Dict[str, Any]:
    server_doc = _minimal_server_record(doc)
    return {
        "op_id": op_id,
        "status": "conflict",
        "entity_type": doctype,
        "entity_id": doc.client_id,
        "client_base_version": client_base_version,
        "server_doc": server_doc,
        "server_doc_version": doc.doc_version,
        # Backward-compatible aliases for existing clients.
        "client_id": doc.client_id,
        "doc_version": doc.doc_version,
        "server_modified": _to_iso(doc.server_modified),
        "server_record": server_doc,
    }

def _get_op_result(user: str, device_id: str, wallet_id: str, op_id: str) -> Optional[Dict[str, Any]]:
    if not device_id or not op_id:
        return None
    result_json = frappe.get_value(
        "Hisabi Sync Op",
        {"user": user, "device_id": device_id, "op_id": _ledger_op_id(wallet_id, op_id)},
        "result_json",
    )
    if not result_json:
        return None
    try:
        return json.loads(result_json)
    except json.JSONDecodeError:
        return None


def _write_audit_log(
    *,
    user: str,
    device_id: str,
    op_id: str,
    entity_type: str,
    entity_client_id: str,
    status: str,
    payload: Dict[str, Any],
) -> None:
    try:
        audit = frappe.new_doc("Hisabi Audit Log")
        audit.user = user
        audit.device_id = device_id
        audit.op_id = op_id
        audit.entity_type = entity_type
        audit.entity_client_id = entity_client_id
        audit.status = status
        audit.payload_json = json.dumps(payload, ensure_ascii=False)
        audit.save(ignore_permissions=True)
    except Exception:
        frappe.log_error("Failed to write audit log", "hisabi_backend.sync")


def _log_rejected_sync_item(
    *,
    user: str,
    device_id: str,
    wallet_id: str,
    op_id: Optional[str],
    entity_type: Optional[str],
    client_id: Optional[str],
    reason: str,
    payload: Dict[str, Any],
) -> None:
    code = str(reason or "").strip() or "rejected"
    if code in {"wallet_id_mismatch", "entity_id_mismatch"}:
        frappe.logger("hisabi_backend.sync").warning(
            "sync_push_rejected",
            extra={
                "reason": code,
                "wallet_id": wallet_id,
                "entity_type": entity_type,
                "client_id": client_id,
                "op_id": op_id,
                "device_id": device_id,
                "user": user,
            },
        )

    _write_audit_log(
        user=user,
        device_id=device_id,
        op_id=op_id or "",
        entity_type=entity_type or "",
        entity_client_id=client_id or "",
        status="rejected",
        payload={
            "reason": code,
            "wallet_id": wallet_id,
            "payload": payload,
        },
    )


def _check_rate_limit(device_id: str) -> None:
    cache = frappe.cache()
    key = f"hisabi_sync_rate:{device_id}"
    max_requests = cint(frappe.conf.get("hisabi_sync_rate_limit_max", RATE_LIMIT_MAX))
    window_sec = cint(frappe.conf.get("hisabi_sync_rate_limit_window_sec", RATE_LIMIT_WINDOW_SEC))
    current = cint(cache.get_value(key) or 0)
    if current >= max_requests:
        frappe.throw(_("Rate limit exceeded"), frappe.PermissionError)
    cache.set_value(key, current + 1, expires_in_sec=window_sec)


@frappe.whitelist(allow_guest=False)
def sync_push(
    device_id: Optional[str] = None,
    wallet_id: Optional[str] = None,
    items: Optional[List[Dict[str, Any]] | str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Apply client changes to the server."""
    frappe.flags.disable_traceback = True
    request = getattr(frappe, "request", None)
    form_dict = getattr(frappe, "form_dict", {}) or {}
    json_body = None
    request_form = getattr(request, "form", {}) or {}
    request_args = getattr(request, "args", {}) or {}

    if request:
        try:
            json_body = request.get_json(silent=True)
        except Exception:
            json_body = None

    if device_id is None:
        device_id = form_dict.get("device_id")
    if wallet_id is None:
        wallet_id = form_dict.get("wallet_id")
    if items is None:
        items = form_dict.get("items")

    if request:
        if device_id is None:
            device_id = request_form.get("device_id") or request_args.get("device_id")
        if wallet_id is None:
            wallet_id = request_form.get("wallet_id") or request_args.get("wallet_id")
        if items is None:
            items = request_form.get("items") or request_args.get("items")

    if device_id is None and json_body:
        device_id = json_body.get("device_id")
    if wallet_id is None and json_body:
        wallet_id = json_body.get("wallet_id")
    if items is None and json_body:
        items = json_body.get("items")

    if items is None and request:
        request_data = getattr(request, "data", b"") or b""
        if isinstance(request_data, str):
            request_data = request_data.encode("utf-8")
        raw = request_data.decode("utf-8") if request_data else ""
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                if device_id is None:
                    device_id = parsed.get("device_id")
                if wallet_id is None:
                    wallet_id = parsed.get("wallet_id")
                if items is None:
                    items = parsed.get("items")

    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            items = None

    if not device_id:
        return _build_sync_error("device_id_required", "device_id is required", status_code=417)
    if not wallet_id:
        return _build_sync_error("wallet_id_required", "wallet_id is required", status_code=417)
    if items is None:
        return _build_sync_error("items_required", "items is required", status_code=417)

    try:
        user, device = _require_device_auth(device_id)
        _check_rate_limit(device_id)
        wallet_id = validate_client_id(wallet_id)
    except Exception as exc:
        frappe.clear_last_message()
        return _build_sync_error(
            "auth_failed",
            str(exc) or "sync auth failed",
            status_code=_sync_status_for_exception(exc),
        )

    if not isinstance(items, list):
        return _build_sync_error("items_invalid", "items must be a list", status_code=417)

    if len(items) > MAX_PUSH_ITEMS:
        return _build_sync_response(
            {"message": {"results": [{"status": "error", "error": "too_many_items"}], "server_time": now_datetime().isoformat()}},
            status_code=417,
        )

    results: List[Dict[str, Any]] = []
    affected_accounts = set()
    affected_multi_parents = set()
    affected_budgets = set()
    affected_goals = set()
    affected_debts = set()
    affected_jameyas = set()
    deleted_accounts = set()
    budgets_dirty = False
    goals_dirty = False

    for item in items:
        validation_error = _validate_sync_push_item(item, wallet_id)
        if validation_error and validation_error.get("error") in {"unsupported_entity_type", "doctype_not_installed"}:
            return _build_sync_response({"error": validation_error.get("error")}, status_code=417)

    # Wallet creation via sync: allow create for Hisabi Wallet even if user is not yet a member.
    # For all other mutations, require membership (viewer is read-only).
    member_info = None
    try:
        if any((i.get("entity_type") or "") != "Hisabi Wallet" for i in items):
            member_info = require_wallet_member(wallet_id, user, min_role="viewer")
    except Exception as exc:
        frappe.clear_last_message()
        return _build_sync_error(
            "wallet_access_denied",
            str(exc) or "wallet access denied",
            status_code=_sync_status_for_exception(exc),
        )

    if member_info and member_info.role == "viewer":
        # Viewer is read-only: block all mutations.
        for i in items:
            if (i.get("entity_type") or "") != "Hisabi Wallet Member":
                return _build_sync_error(
                    "wallet_read_only",
                    "Viewer role cannot sync_push mutations",
                    status_code=403,
                )

    for item in items:
        op_id = item.get("op_id")
        entity_type = item.get("entity_type")
        entity_id = item.get("entity_id")
        payload = item.get("payload") or {}
        client_id = payload.get("client_id") or entity_id

        # Sync reliability: retries must be safe; op_id provides idempotency.
        if isinstance(op_id, str) and op_id.strip() and _check_duplicate_op(user, device_id, wallet_id, op_id):
            stored = _get_op_result(user, device_id, wallet_id, op_id)
            if stored:
                duplicate_result = dict(stored)
                duplicate_result["already_applied"] = True
                results.append(duplicate_result)
            else:
                results.append(
                    {
                        "status": _get_op_status(user, device_id, wallet_id, op_id) or "accepted",
                        "already_applied": True,
                        "op_id": op_id,
                        "entity_type": entity_type,
                        "client_id": client_id,
                    }
                )
            continue

        validation_error = _validate_sync_push_item(item, wallet_id)
        if validation_error:
            if validation_error.get("error") in {"unsupported_entity_type", "doctype_not_installed"}:
                return _build_sync_response({"error": validation_error.get("error")}, status_code=417)
            results.append(validation_error)
            _log_rejected_sync_item(
                user=user,
                device_id=device_id,
                wallet_id=wallet_id,
                op_id=op_id if isinstance(op_id, str) else None,
                entity_type=entity_type if isinstance(entity_type, str) else None,
                client_id=client_id if isinstance(client_id, str) else None,
                reason=str(validation_error.get("error") or "rejected"),
                payload=item,
            )
            continue

        operation = item.get("operation")
        base_version = item.get("base_version")

        existing = _get_doc_by_client_id(entity_type, user, client_id, wallet_id=wallet_id)

        if operation == "create" and existing:
            doc = existing
            if entity_type == "Hisabi Account":
                doc = _prepare_doc_for_write(entity_type, payload, user, existing=existing)
                doc.save(ignore_permissions=True)
            result = {
                "status": "accepted",
                "op_id": op_id,
                "entity_type": entity_type,
                "entity_id": doc.name,
                "client_id": doc.client_id,
                "doc_version": doc.doc_version,
                "server_modified": _to_iso(doc.server_modified),
            }
            results.append(result)
            _store_op_id(
                user=user,
                device_id=device_id,
                wallet_id=wallet_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="duplicate",
                payload=item,
                result=result,
            )
            _write_audit_log(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="duplicate",
                payload=item,
            )
            continue

        if operation in {"update", "delete"} and not existing:
            result = _build_item_error(
                error_code="not_found",
                entity_type=entity_type,
                client_id=client_id,
            )
            results.append(result)
            _store_op_id(
                user=user,
                device_id=device_id,
                wallet_id=wallet_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="error",
                payload=item,
                result=result,
            )
            _write_audit_log(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="error",
                payload=item,
            )
            continue

        if existing and base_version is not None:
            # Concurrency: prevent blind overwrites across devices.
            if int(base_version) != int(existing.doc_version or 0):
                conflict = _conflict_response(
                    entity_type,
                    existing,
                    op_id=op_id or "",
                    client_base_version=int(base_version),
                )
                results.append(conflict)
                _store_op_id(
                    user=user,
                    device_id=device_id,
                    wallet_id=wallet_id,
                    op_id=op_id or "",
                    entity_type=entity_type,
                    entity_client_id=client_id,
                    status="conflict",
                    payload=item,
                    result=conflict,
                )
                _write_audit_log(
                    user=user,
                    device_id=device_id,
                    op_id=op_id or "",
                    entity_type=entity_type,
                    entity_client_id=client_id,
                    status="conflict",
                    payload=item,
                )
                continue

        payload_json = frappe.as_json(payload)
        if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            results.append(
                _build_item_error(
                    error_code="payload_too_large",
                    entity_type=entity_type,
                    client_id=client_id,
                )
            )
            continue

        # Enforce wallet scoping server-side.
        meta = frappe.get_meta(entity_type)
        if meta.has_field("wallet_id"):
            payload = dict(payload)
            payload["wallet_id"] = wallet_id

        # Wallet creation special case: create wallet + owner member.
        if entity_type == "Hisabi Wallet" and operation == "create":
            if client_id != wallet_id:
                wallet_error = _build_item_error(
                    error_code="wallet_id_must_equal_client_id",
                    entity_type=entity_type,
                    client_id=client_id,
                )
                results.append(wallet_error)
                _log_rejected_sync_item(
                    user=user,
                    device_id=device_id,
                    wallet_id=wallet_id,
                    op_id=op_id if isinstance(op_id, str) else None,
                    entity_type=entity_type if isinstance(entity_type, str) else None,
                    client_id=client_id if isinstance(client_id, str) else None,
                    reason="wallet_id_mismatch",
                    payload={"item": item, "validation_error": wallet_error},
                )
                continue

        if entity_type != "Hisabi Wallet":
            # For all wallet-scoped entities, require at least member role.
            try:
                require_wallet_member(wallet_id, user, min_role="member")
            except Exception as exc:
                frappe.clear_last_message()
                results.append(
                    _build_item_error(
                        error_code="wallet_access_denied",
                        entity_type=entity_type,
                        client_id=client_id,
                        detail=str(exc) or "wallet access denied",
                    )
                )
                continue

        try:
            prev_tx_account = None
            prev_tx_to_account = None
            if entity_type == "Hisabi Transaction" and existing:
                # Invariant: recalc must cover both pre-update and post-update links.
                prev_tx_account = getattr(existing, "account", None)
                prev_tx_to_account = getattr(existing, "to_account", None)
                if prev_tx_account:
                    prev_account_doc = _resolve_account_doc(prev_tx_account, user=user, wallet_id=wallet_id)
                    if prev_account_doc and prev_account_doc.parent_account:
                        affected_multi_parents.add(prev_account_doc.parent_account)
                if prev_tx_to_account:
                    prev_to_account_doc = _resolve_account_doc(prev_tx_to_account, user=user, wallet_id=wallet_id)
                    if prev_to_account_doc and prev_to_account_doc.parent_account:
                        affected_multi_parents.add(prev_to_account_doc.parent_account)

            if entity_type in {"Hisabi Budget", "Hisabi Goal"}:
                payload = dict(payload)
                base_currency = _resolve_base_currency(wallet_id, user)
                if entity_type == "Hisabi Budget":
                    if not payload.get("currency") and base_currency:
                        payload["currency"] = base_currency
                    if payload.get("amount") is None and payload.get("amount_base") is not None:
                        payload["amount"] = payload.get("amount_base")
                if entity_type == "Hisabi Goal":
                    if not payload.get("currency") and base_currency:
                        payload["currency"] = base_currency
                    if payload.get("target_amount") is None and payload.get("target_amount_base") is not None:
                        payload["target_amount"] = payload.get("target_amount_base")

            if entity_type == "Hisabi Account":
                payload = dict(payload)
                payload["currency"] = _normalize_account_currency(payload.get("currency"))
                payload["base_currency"] = _normalize_account_currency(payload.get("base_currency"))
                is_multi_currency = cint(payload.get("is_multi_currency") or 0) == 1
                if is_multi_currency:
                    payload["is_multi_currency"] = 1
                    payload["base_currency"] = payload.get("base_currency") or payload.get("currency") or _normalize_account_currency(
                        _resolve_base_currency(wallet_id, user)
                    )
                    payload["currency"] = payload.get("currency") or payload.get("base_currency")
                    payload["group_id"] = _normalize_group_id(payload.get("group_id")) or str(client_id or uuid.uuid4())
                    payload["parent_account"] = None
                elif payload.get("parent_account"):
                    payload["is_multi_currency"] = 0
                    parent_doc = _resolve_account_doc(payload.get("parent_account"), user=user, wallet_id=wallet_id)
                    if parent_doc:
                        payload["parent_account"] = parent_doc.name
                        payload["group_id"] = _normalize_group_id(payload.get("group_id")) or _normalize_group_id(parent_doc.group_id)
                        payload["base_currency"] = payload.get("base_currency") or _normalize_account_currency(
                            parent_doc.base_currency or parent_doc.currency
                        )
                elif not payload.get("base_currency") and payload.get("currency"):
                    payload["base_currency"] = payload.get("currency")

            if entity_type == "Hisabi Transaction":
                payload = dict(payload)
                payload["currency"] = _normalize_account_currency(payload.get("currency"))
                payload, routed_parents = _resolve_multi_currency_transaction_accounts(
                    payload,
                    user=user,
                    wallet_id=wallet_id,
                )
                if routed_parents:
                    affected_multi_parents.update(routed_parents)
                payload = _hydrate_transaction_fx_fields(payload, user=user, wallet_id=wallet_id)
                if payload.get("base_amount") in (None, "") and payload.get("amount_base") not in (None, ""):
                    payload["base_amount"] = payload.get("amount_base")

            doc = _prepare_doc_for_write(entity_type, payload, user, existing=existing)
            fx_sanity_warnings: List[str] = []
            if entity_type == "Hisabi Transaction":
                fx_sanity_warnings = _collect_transaction_fx_sanity_warnings(doc)
            mark_deleted = operation == "delete"

            if entity_type == "Hisabi Account" and not existing:
                if _is_multi_currency_parent(doc):
                    doc.opening_balance = 0
                    doc.current_balance = 0
                elif not doc.current_balance and doc.opening_balance is not None:
                    doc.current_balance = doc.opening_balance

            # Keep doc_version monotonic for every accepted mutation.
            apply_common_sync_fields(doc, payload, bump_version=True, mark_deleted=mark_deleted)
            # Data integrity: soft delete must sync like any other write.
            if mark_deleted and doc.meta.has_field("deleted_at") and not doc.deleted_at:
                doc.deleted_at = now_datetime()
            if entity_type == "Hisabi Account" and operation == "delete":
                doc.is_deleted = 1
                if not doc.deleted_at:
                    doc.deleted_at = now_datetime()
            doc.save(ignore_permissions=True)
            settings_fx_seed_summary: Optional[Dict[str, Any]] = None
            if entity_type == "Hisabi Settings" and not mark_deleted:
                # Seed wallet-scoped default FX rows once settings declare wallet currencies.
                # Custom/API rows are never overwritten by this helper.
                try:
                    settings_fx_seed_summary = seed_wallet_default_fx_rates(
                        wallet_id=wallet_id,
                        user=user,
                        base_currency=getattr(doc, "base_currency", None),
                        enabled_currencies=getattr(doc, "enabled_currencies", None),
                        overwrite_defaults=False,
                    )
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "hisabi_backend.sync_seed_fx_defaults")
            if entity_type == "Hisabi Account" and doc.name != doc.client_id:
                doc = _rename_doc_to_client_id(doc, doc.client_id)
            if entity_type == "Hisabi Account" and operation == "delete":
                if not doc.deleted_at:
                    doc.deleted_at = now_datetime()
                if doc.is_deleted != 1:
                    doc.is_deleted = 1
                doc.db_set("is_deleted", 1, update_modified=False)
                doc.db_set("deleted_at", doc.deleted_at, update_modified=False)
                deleted_accounts.add(doc.name)
            if entity_type == "Hisabi Category" and doc.name != doc.client_id:
                # Category IDs are used as sync keys across clients; enforce canonical rename.
                doc = _rename_doc_to_client_id(doc, doc.client_id)
            if entity_type in {"Hisabi Transaction Allocation", "Hisabi Transaction Bucket"}:
                _sync_transaction_bucket_mirror(doc, entity_type, operation=operation)

            if entity_type == "Hisabi Wallet" and operation == "create":
                # Ensure membership row exists for owner.
                if not frappe.db.exists("Hisabi Wallet Member", {"wallet": wallet_id, "user": user}):
                    m = frappe.new_doc("Hisabi Wallet Member")
                    m.wallet = wallet_id
                    m.user = user
                    m.role = "owner"
                    m.status = "active"
                    apply_common_sync_fields(m, bump_version=True, mark_deleted=False)
                    m.save(ignore_permissions=True)

            if entity_type == "Hisabi Transaction":
                if prev_tx_account:
                    affected_accounts.add(prev_tx_account)
                if prev_tx_to_account:
                    affected_accounts.add(prev_tx_to_account)
                if doc.account:
                    affected_accounts.add(doc.account)
                    source_account_doc = _resolve_account_doc(doc.account, user=user, wallet_id=wallet_id)
                    if source_account_doc and source_account_doc.parent_account:
                        affected_multi_parents.add(source_account_doc.parent_account)
                if doc.to_account:
                    affected_accounts.add(doc.to_account)
                    to_account_doc = _resolve_account_doc(doc.to_account, user=user, wallet_id=wallet_id)
                    if to_account_doc and to_account_doc.parent_account:
                        affected_multi_parents.add(to_account_doc.parent_account)
                budgets_dirty = True
                goals_dirty = True

            if entity_type == "Hisabi Account":
                if _is_multi_currency_parent(doc):
                    _create_base_child_for_multi_currency_parent(doc, user=user)
                    affected_multi_parents.add(doc.name)
                if doc.parent_account:
                    affected_multi_parents.add(doc.parent_account)
                if operation == "update":
                    affected_accounts.add(doc.name)
                goals_dirty = True

            if entity_type == "Hisabi Budget":
                affected_budgets.add(doc.name)

            if entity_type == "Hisabi Goal":
                affected_goals.add(doc.name)

            if entity_type == "Hisabi Debt":
                affected_debts.add(doc.name)
                goals_dirty = True

            if entity_type == "Hisabi Debt Installment":
                if doc.debt:
                    affected_debts.add(doc.debt)
                    goals_dirty = True

            if entity_type == "Hisabi Jameya":
                affected_jameyas.add(doc.name)

            if entity_type == "Hisabi Jameya Payment":
                if doc.jameya:
                    affected_jameyas.add(doc.jameya)

            result = {
                "status": "accepted",
                "op_id": op_id,
                "entity_type": entity_type,
                "entity_id": doc.name,
                "client_id": doc.client_id,
                "doc_version": doc.doc_version,
                "server_modified": _to_iso(doc.server_modified),
            }
            if fx_sanity_warnings:
                result["warnings"] = fx_sanity_warnings
            if settings_fx_seed_summary and (
                cint(settings_fx_seed_summary.get("inserted") or 0) > 0
                or cint(settings_fx_seed_summary.get("updated") or 0) > 0
            ):
                result["fx_seed"] = settings_fx_seed_summary
            results.append(result)

            _store_op_id(
                user=user,
                device_id=device_id,
                wallet_id=wallet_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=doc.client_id,
                status="accepted",
                payload=item,
                result=result,
            )
            _write_audit_log(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=doc.client_id,
                status="accepted",
                payload=item,
            )
        except Exception as exc:
            # Reliability: never crash the whole batch for one bad mutation; return structured rejection.
            frappe.log_error(frappe.get_traceback(), "hisabi_backend.sync_push_item")
            rejected = _build_item_rejected(
                op_id=op_id if isinstance(op_id, str) else None,
                entity_type=entity_type if isinstance(entity_type, str) else None,
                client_id=client_id if isinstance(client_id, str) else None,
                detail=str(exc) or exc.__class__.__name__,
            )
            results.append(rejected)
            try:
                _store_op_id(
                    user=user,
                    device_id=device_id,
                    wallet_id=wallet_id,
                    op_id=op_id or "",
                    entity_type=entity_type,
                    entity_client_id=client_id,
                    status="error",
                    payload=item,
                    result=rejected,
                )
            except Exception:
                frappe.log_error(frappe.get_traceback(), "hisabi_backend.sync_push_item_store_rejected")
            _write_audit_log(
                user=user,
                device_id=device_id,
                op_id=op_id or "",
                entity_type=entity_type,
                entity_client_id=client_id,
                status="error",
                payload=item,
            )
            continue

    for account_name in affected_accounts:
        _recalculate_account_balance(user, account_name, wallet_id=wallet_id)

    for parent_account_name in affected_multi_parents:
        _recalculate_multi_currency_parent_balance(
            user=user,
            wallet_id=wallet_id,
            parent_account_id=parent_account_name,
        )

    if affected_debts:
        recalc_debts(user, affected_debts)

    if budgets_dirty and not affected_budgets:
        affected_budgets = {
            b.name for b in frappe.get_all("Hisabi Budget", filters={"wallet_id": wallet_id, "is_deleted": 0})
        }
    if affected_budgets:
        recalc_budgets(user, affected_budgets)

    if goals_dirty and not affected_goals:
        affected_goals = {g.name for g in frappe.get_all("Hisabi Goal", filters={"wallet_id": wallet_id, "is_deleted": 0})}
    if affected_goals:
        recalc_goals(user, affected_goals)

    if affected_jameyas:
        recalc_jameyas(user, affected_jameyas)

    for account_name in deleted_accounts:
        try:
            acc = frappe.get_doc("Hisabi Account", account_name)
        except Exception:
            continue
        if not acc.deleted_at:
            acc.deleted_at = now_datetime()
        if acc.is_deleted != 1:
            acc.is_deleted = 1
        acc.db_set("is_deleted", 1, update_modified=False)
        acc.db_set("deleted_at", acc.deleted_at, update_modified=False)

    device.last_sync_at = now_datetime()
    device.last_sync_ms = min(int(device.last_sync_at.timestamp() * 1000), 2147483647)
    device.save(ignore_permissions=True)

    return _build_sync_response(
        {"message": {"results": results, "server_time": now_datetime().isoformat()}},
        status_code=200,
    )


def _build_sync_pull_seed_warnings(wallet_id: str) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    if not wallet_id:
        return warnings

    seed_doctypes = ("Hisabi Account", "Hisabi Category")
    missing: List[str] = []
    for doctype in seed_doctypes:
        if not frappe.db.exists("DocType", doctype):
            continue
        count = cint(frappe.db.count(doctype, {"wallet_id": wallet_id, "is_deleted": 0}) or 0)
        if count == 0:
            missing.append(doctype)

    if missing:
        warnings.append(
            {
                "code": "seed_records_empty",
                "message": "تحذير: سجلات البذور الأساسية فارغة على الخادم، لا تحذف بذور العميل تلقائياً قبل التحقق اليدوي.",
                "wallet_id": wallet_id,
                "doctypes": missing,
            }
        )
    return warnings


@frappe.whitelist(allow_guest=False)
def sync_pull(
    device_id: Optional[str] = None,
    wallet_id: Optional[str] = None,
    since: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Return server changes since cursor using server_modified."""

    def _coerce_json(value: Any) -> Any:
        if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
            return str(value)
        if isinstance(value, dict):
            return {str(k): _coerce_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_coerce_json(v) for v in value]
        return value

    def _parse_since_value(value: Any) -> Optional[datetime.datetime]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.lstrip("-").isdigit():
                try:
                    numeric = int(stripped)
                except ValueError:
                    return None
                seconds = numeric / 1000 if numeric >= 10**12 else numeric
                try:
                    return _cursor_dt(datetime.datetime.utcfromtimestamp(seconds))
                except (OverflowError, OSError, ValueError):
                    return None
        elif isinstance(value, (int, float)):
            numeric = int(value)
            seconds = numeric / 1000 if numeric >= 10**12 else numeric
            try:
                return _cursor_dt(datetime.datetime.utcfromtimestamp(seconds))
            except (OverflowError, OSError, ValueError):
                return None

        return _cursor_dt(value)

    def _parse_cursor_tuple(value: Any) -> Optional[Tuple[datetime.datetime, str, str]]:
        if value is None:
            return None
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if "|" in raw:
                parts = raw.split("|", 2)
                if len(parts) == 3:
                    ts = _parse_since_value(parts[0])
                    if ts:
                        return (ts, parts[1], parts[2])
            ts = _parse_since_value(raw)
            if ts:
                return (ts, "", "")
            return None
        ts = _parse_since_value(value)
        if ts:
            return (ts, "", "")
        return None

    def _encode_cursor_tuple(cursor_tuple: Tuple[datetime.datetime, str, str]) -> str:
        dt, doctype, name = cursor_tuple
        normalized_dt = _cursor_dt(dt) or _cursor_dt(now_datetime())
        return f"{normalized_dt.isoformat()}|{doctype}|{name}"

    from urllib.parse import parse_qs

    form_dict = frappe.form_dict or {}
    request = getattr(frappe.local, "request", None)
    json_body = None

    if device_id is None:
        device_id = form_dict.get("device_id")
    if wallet_id is None:
        wallet_id = form_dict.get("wallet_id")
    if since is None:
        since = form_dict.get("since")
    if cursor is None:
        cursor = form_dict.get("cursor")
    if limit is None:
        limit = form_dict.get("limit")

    request_args = getattr(request, "args", {}) or {}
    if request and request_args:
        if device_id is None:
            device_id = request_args.get("device_id")
        if wallet_id is None:
            wallet_id = request_args.get("wallet_id")
        if since is None:
            since = request_args.get("since")
        if cursor is None:
            cursor = request_args.get("cursor")
        if limit is None:
            limit = request_args.get("limit")

    request_query = getattr(request, "query_string", None)
    if request and request_query:
        if isinstance(request_query, bytes):
            parsed = parse_qs(request_query.decode("utf-8"))
        else:
            parsed = parse_qs(str(request_query))
        if device_id is None:
            device_id = (parsed.get("device_id") or [None])[0]
        if wallet_id is None:
            wallet_id = (parsed.get("wallet_id") or [None])[0]
        if since is None:
            since = (parsed.get("since") or [None])[0]
        if cursor is None:
            cursor = (parsed.get("cursor") or [None])[0]
        if limit is None:
            limit = (parsed.get("limit") or [None])[0]

    request_data = getattr(request, "data", None)
    request_content_type = getattr(request, "content_type", None)
    if request and request_data and request_content_type and "application/json" in request_content_type:
        if isinstance(request_data, str):
            raw = request_data
        else:
            raw = request_data.decode("utf-8")
        if raw:
            try:
                json_body = json.loads(raw)
            except json.JSONDecodeError:
                json_body = None

    if isinstance(json_body, dict):
        if device_id is None:
            device_id = json_body.get("device_id")
        if wallet_id is None:
            wallet_id = json_body.get("wallet_id")
        if since is None:
            since = json_body.get("since")
        if cursor is None:
            cursor = json_body.get("cursor")
        if limit is None:
            limit = json_body.get("limit")

    if not device_id:
        return _build_sync_error("device_id_required", "device_id is required", status_code=417)
    if not wallet_id:
        return _build_sync_error("wallet_id_required", "wallet_id is required", status_code=417)

    try:
        user, device = _require_device_auth(device_id)
        wallet_id = validate_client_id(wallet_id)
        require_wallet_member(wallet_id, user, min_role="viewer")
    except Exception as exc:
        frappe.clear_last_message()
        return _build_sync_error(
            "auth_failed",
            str(exc) or "sync auth failed",
            status_code=_sync_status_for_exception(exc),
        )
    try:
        parsed_limit = int(limit or 500)
    except (TypeError, ValueError):
        parsed_limit = 500
    limit = min(max(parsed_limit, 1), 500)

    cursor_value = cursor or since
    cursor_tuple = _parse_cursor_tuple(cursor_value) if cursor_value else None
    if cursor_value and not cursor_tuple:
        return _build_sync_error("invalid_cursor", "invalid_cursor", status_code=417)

    candidates: List[Dict[str, Any]] = []

    per_doctype_target = limit + 1

    for doctype in DOCTYPE_LIST:
        if not frappe.db.exists("DocType", doctype):
            continue
        meta = frappe.get_meta(doctype)
        if not meta.has_field("server_modified"):
            continue

        filters: Dict[str, Any] = {}
        if doctype == "Hisabi Wallet":
            filters["name"] = wallet_id
        elif doctype == "Hisabi Wallet Member":
            filters["wallet"] = wallet_id
        elif meta.has_field("wallet_id"):
            filters["wallet_id"] = wallet_id
        elif meta.has_field("user"):
            filters["user"] = user
        else:
            filters["owner"] = user

        if cursor_tuple:
            filters["server_modified"] = [">=", cursor_tuple[0]]

        fields = ["name", "server_modified"]
        order_secondary_field = "name"
        if meta.has_field("client_id"):
            fields.append("client_id")
            order_secondary_field = "client_id"

        # Sync pagination: keep scanning each doctype until we collect limit+1 post-cursor rows.
        # This prevents cursor-boundary rows from masking newer rows and incorrectly flipping has_more.
        start = 0
        page_length = max(per_doctype_target, 50)
        order_by = f"server_modified asc, {order_secondary_field} asc, name asc"
        doctype_candidates: List[Dict[str, Any]] = []
        while len(doctype_candidates) < per_doctype_target:
            records = frappe.get_all(
                doctype,
                filters=filters,
                limit_start=start,
                limit_page_length=page_length,
                order_by=order_by,
                fields=fields,
            )
            if not records:
                break

            for row in records:
                server_modified_dt = _cursor_dt(row.server_modified)
                if not server_modified_dt:
                    continue
                cursor_entity_id = row.get("client_id") or row.name
                key = (server_modified_dt, doctype, cursor_entity_id)
                if cursor_tuple and key <= cursor_tuple:
                    continue
                doctype_candidates.append(
                    {
                        "doctype": doctype,
                        "name": row.name,
                        "entity_id": cursor_entity_id,
                        "server_modified": server_modified_dt,
                        "key": key,
                    }
                )
                if len(doctype_candidates) >= per_doctype_target:
                    break

            if len(records) < page_length:
                break
            start += len(records)

        candidates.extend(doctype_candidates)

    # Sync pagination: prevent missing/duplicate pages.
    candidates.sort(key=lambda row: row["key"])
    selected = candidates[:limit]
    has_more = len(candidates) > limit
    items: List[Dict[str, Any]] = []

    for row in selected:
        doctype = row["doctype"]
        doc = _sanitize_pull_record(doctype, frappe.get_doc(doctype, row["name"]).as_dict())
        if doctype == "Hisabi Account":
            doc = _enrich_multi_currency_account_payload(doc, user=user, wallet_id=wallet_id)
        doc = _coerce_json(doc)
        client_id = doc.get("client_id") or doc.get("name")
        items.append(
            {
                "entity_type": doctype,
                "entity_id": client_id,
                "client_id": client_id,
                "doc_version": doc.get("doc_version"),
                "server_modified": _to_iso(doc.get("server_modified")),
                "payload": doc,
                "is_deleted": doc.get("is_deleted"),
                "deleted_at": _to_iso(doc.get("deleted_at")),
            }
        )

    if selected:
        last_key = selected[-1]["key"]
        next_cursor = _encode_cursor_tuple(last_key)
    elif cursor_tuple:
        next_cursor = _encode_cursor_tuple(cursor_tuple)
    else:
        next_cursor = _encode_cursor_tuple((now_datetime(), "", ""))

    device.last_pull_at = now_datetime()
    device.last_pull_ms = min(int(device.last_pull_at.timestamp() * 1000), 2147483647)
    device.db_set("last_pull_at", device.last_pull_at, update_modified=False)
    device.db_set("last_pull_ms", device.last_pull_ms, update_modified=False)

    warnings = _build_sync_pull_seed_warnings(wallet_id)
    return _build_sync_response(
        {
            "message": {
                "items": items,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "server_time": now_datetime().isoformat(),
                "warnings": warnings,
            }
        },
        status_code=200,
    )
