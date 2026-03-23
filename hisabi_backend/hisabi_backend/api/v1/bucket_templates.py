"""Bucket template APIs."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_client_id
from hisabi_backend.utils.wallet_acl import require_wallet_member

DEFAULT_BUCKETS: List[Dict[str, Any]] = [
    {"key": "personal", "name": "الشخصية", "color": "#3b82f6", "icon": "user", "sort_order": 0},
    {"key": "commitments", "name": "الالتزامات", "color": "#f59e0b", "icon": "home", "sort_order": 1},
    {"key": "savings", "name": "الادخار", "color": "#22c55e", "icon": "piggy-bank", "sort_order": 2},
    {"key": "investment", "name": "الاستثمار", "color": "#8b5cf6", "icon": "trending-up", "sort_order": 3},
    {"key": "health", "name": "الصحة", "color": "#ef4444", "icon": "heart-pulse", "sort_order": 4},
    {"key": "charity", "name": "الصدقات", "color": "#14b8a6", "icon": "heart", "sort_order": 5},
]

DEFAULT_TEMPLATE_DISTRIBUTION: List[Dict[str, Any]] = [
    {"key": "personal", "percentage": 50},
    {"key": "commitments", "percentage": 30},
    {"key": "savings", "percentage": 15},
    {"key": "investment", "percentage": 5},
]

DEFAULT_BUCKET_CATEGORY_MAPPINGS: Dict[str, List[str]] = {
    "personal": [
        "cat-food", "cat-groceries", "cat-restaurants", "cat-coffee", "cat-delivery",
        "cat-shopping", "cat-clothes", "cat-electronics", "cat-gifts", "cat-personal-care",
        "cat-entertainment", "cat-subscriptions", "cat-movies", "cat-games", "cat-hobbies",
        "cat-transport", "cat-fuel", "cat-rideshare", "cat-parking", "cat-public-transport",
    ],
    "commitments": [
        "cat-housing", "cat-rent", "cat-utilities", "cat-maintenance", "cat-furniture",
        "cat-family", "cat-kids", "cat-family-support",
        "cat-telecom", "cat-mobile", "cat-internet",
        "cat-financial", "cat-bank-fees", "cat-loan-payment", "cat-insurance",
        "cat-car-maintenance",
    ],
    "savings": ["cat-travel", "cat-flights", "cat-hotels", "cat-travel-food"],
    "investment": ["cat-education", "cat-school-fees", "cat-books", "cat-courses"],
    "charity": ["cat-zakat"],
    "health": ["cat-health", "cat-doctor", "cat-medicine", "cat-gym", "cat-insurance-health"],
}

DEFAULT_BUCKET_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "personal": ["طعام", "بقال", "مطعم", "قهو", "مشروب", "تسوق", "ملابس", "هدية", "ترفيه", "مواصل", "وقود", "توصيل"],
    "commitments": ["ايجار", "إيجار", "فاتور", "كهرب", "ماء", "عائل", "اسر", "أسر", "جوال", "هاتف", "انترنت", "إنترنت", "قرض", "قسط", "تامين", "تأمين", "صيانه", "صيانة"],
    "savings": ["سفر", "رحل", "فندق", "حجز", "ادخار"],
    "investment": ["تعليم", "دراس", "جامع", "كتب", "دور", "استثمار"],
    "charity": ["زكاة", "صدق", "تبرع", "خيري"],
    "health": ["صحه", "صحة", "طبيب", "مستشف", "دواء", "صيدلي", "علاج", "رياض"],
}


def _normalize_bucket_label(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ة", "ه")
        .replace("ى", "ي")
        .lower()
    )


def _resolve_category_bucket_key(client_id: Any, category_name: Any, kind: Any) -> Optional[str]:
    if str(kind or "expense") != "expense":
        return None

    normalized_client_id = str(client_id or "").strip()
    if normalized_client_id:
        for bucket_key, category_ids in DEFAULT_BUCKET_CATEGORY_MAPPINGS.items():
            if normalized_client_id in category_ids:
                return bucket_key

    normalized_name = _normalize_bucket_label(category_name)
    if not normalized_name:
        return None

    for bucket_key, keywords in DEFAULT_BUCKET_CATEGORY_KEYWORDS.items():
        if any(_normalize_bucket_label(keyword) in normalized_name for keyword in keywords):
            return bucket_key

    return "personal"


def ensure_wallet_bucket_defaults(wallet_id: str, user: Optional[str] = None) -> Dict[str, int]:
    wallet_id = validate_client_id(wallet_id)
    wallet = frappe.get_doc("Hisabi Wallet", wallet_id)
    owner_user = user or getattr(wallet, "owner_user", None)
    if not owner_user:
        owner_user = frappe.get_value("Hisabi Wallet Member", {"wallet": wallet_id, "status": "active"}, "user")
    if not owner_user:
        frappe.throw(_("Wallet owner not found"), frappe.ValidationError)

    created_buckets = 0
    repaired_categories = 0
    created_template = 0

    bucket_rows = frappe.get_all(
        "Hisabi Bucket",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["name", "title", "bucket_name", "client_id"],
        order_by="sort_order asc, modified asc",
    )
    bucket_id_by_key: Dict[str, str] = {}
    bucket_id_by_name: Dict[str, str] = {}
    for row in bucket_rows:
        normalized_name = _normalize_bucket_label(row.get("title") or row.get("bucket_name"))
        if normalized_name:
            bucket_id_by_name[normalized_name] = row["name"]
        client_id = str(row.get("client_id") or "")
        for definition in DEFAULT_BUCKETS:
            if client_id.endswith(f":bucket:{definition['key']}") or normalized_name == _normalize_bucket_label(definition["name"]):
                bucket_id_by_key[definition["key"]] = row["name"]

    for definition in DEFAULT_BUCKETS:
        if definition["key"] in bucket_id_by_key:
            continue
        doc = frappe.new_doc("Hisabi Bucket")
        doc.user = owner_user
        doc.wallet_id = wallet_id
        doc.client_id = f"{wallet_id}:bucket:{definition['key']}"
        doc.title = definition["name"]
        doc.bucket_name = definition["name"]
        doc.color = definition["color"]
        doc.icon = definition["icon"]
        doc.sort_order = definition["sort_order"]
        doc.is_active = 1
        doc.archived = 0
        apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
        doc.save(ignore_permissions=True)
        bucket_id_by_key[definition["key"]] = doc.name
        bucket_id_by_name[_normalize_bucket_label(definition["name"])] = doc.name
        created_buckets += 1

    has_default_template = bool(
        frappe.db.get_value(
            "Hisabi Bucket Template",
            {"wallet_id": wallet_id, "is_deleted": 0, "is_active": 1, "is_default": 1},
            "name",
        )
    )
    if not has_default_template:
        template = frappe.new_doc("Hisabi Bucket Template")
        template.user = owner_user
        template.wallet_id = wallet_id
        template.client_id = f"{wallet_id}:bucket-template:default"
        template.title = "التوزيع الافتراضي"
        template.is_default = 1
        template.is_active = 1
        template.template_items = []
        for row in DEFAULT_TEMPLATE_DISTRIBUTION:
            bucket_id = bucket_id_by_key.get(row["key"])
            if bucket_id:
                template.append("template_items", {"bucket_id": bucket_id, "percentage": row["percentage"]})
        if template.template_items:
            apply_common_sync_fields(template, bump_version=True, mark_deleted=False)
            template.save(ignore_permissions=True)
            created_template = 1

    valid_bucket_ids = set(bucket_id_by_key.values())
    category_rows = frappe.get_all(
        "Hisabi Category",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "archived": 0},
        fields=["name", "client_id", "category_name", "kind", "default_bucket"],
        order_by="sort_order asc, modified asc",
    )
    for row in category_rows:
        bucket_key = _resolve_category_bucket_key(row.get("client_id"), row.get("category_name"), row.get("kind"))
        bucket_id = bucket_id_by_key.get(bucket_key or "")
        if not bucket_id:
            continue
        current_bucket = str(row.get("default_bucket") or "").strip()
        if current_bucket and current_bucket in valid_bucket_ids:
            continue
        doc = frappe.get_doc("Hisabi Category", row["name"])
        doc.default_bucket = bucket_id
        apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
        doc.save(ignore_permissions=True, ignore_version=True)
        repaired_categories += 1

    return {
        "created_buckets": created_buckets,
        "repaired_categories": repaired_categories,
        "created_default_template": created_template,
    }


def repair_bucket_defaults_for_all_wallets() -> Dict[str, Any]:
    wallet_ids = frappe.get_all("Hisabi Wallet", filters={"is_deleted": 0}, pluck="name")
    results: List[Dict[str, Any]] = []
    total_buckets = 0
    total_categories = 0
    total_templates = 0
    for wallet_id in wallet_ids:
        outcome = ensure_wallet_bucket_defaults(wallet_id)
        total_buckets += cint(outcome.get("created_buckets"))
        total_categories += cint(outcome.get("repaired_categories"))
        total_templates += cint(outcome.get("created_default_template"))
        results.append({"wallet_id": wallet_id, **outcome})
    return {
        "wallets": results,
        "totals": {
            "created_buckets": total_buckets,
            "repaired_categories": total_categories,
            "created_default_templates": total_templates,
        },
    }


def _parse_template_items(raw_items: Any) -> List[Dict[str, Any]]:
    if raw_items is None:
        return []
    if isinstance(raw_items, str):
        raw_items = raw_items.strip()
        if not raw_items:
            return []
        raw_items = json.loads(raw_items)
    if not isinstance(raw_items, list):
        frappe.throw(_("template_items must be a list"), frappe.ValidationError)

    parsed: List[Dict[str, Any]] = []
    for row in raw_items:
        if not isinstance(row, dict):
            frappe.throw(_("template_items rows must be objects"), frappe.ValidationError)
        bucket_id = str(row.get("bucket_id") or row.get("bucketId") or row.get("bucket") or "").strip()
        percentage = flt(row.get("percentage") or row.get("percent") or 0, 6)
        parsed.append({"bucket_id": bucket_id, "percentage": percentage})
    return parsed


def _template_filters(wallet_id: str, *, active_only: bool = False) -> Dict[str, Any]:
    filters: Dict[str, Any] = {"wallet_id": wallet_id, "is_deleted": 0}
    if active_only:
        filters["is_active"] = 1
    return filters


def _get_template_doc(template_id: str, wallet_id: str):
    name = frappe.get_value(
        "Hisabi Bucket Template",
        {
            "wallet_id": wallet_id,
            "is_deleted": 0,
            "name": template_id,
        },
        "name",
    )
    if not name:
        name = frappe.get_value(
            "Hisabi Bucket Template",
            {
                "wallet_id": wallet_id,
                "is_deleted": 0,
                "client_id": template_id,
            },
            "name",
        )
    if not name:
        frappe.throw(_("Bucket template not found"), frappe.DoesNotExistError)
    return frappe.get_doc("Hisabi Bucket Template", name)


def _serialize_template(doc) -> Dict[str, Any]:
    return {
        "id": doc.name,
        "client_id": doc.client_id,
        "wallet_id": doc.wallet_id,
        "title": doc.title,
        "is_default": cint(doc.is_default or 0),
        "is_active": cint(doc.is_active if doc.is_active not in (None, "") else 1),
        "template_items": [
            {
                "bucket_id": row.bucket_id,
                "percentage": flt(row.percentage, 6),
                "idx": cint(row.idx or 0),
            }
            for row in (doc.get("template_items") or [])
        ],
        "doc_version": cint(doc.doc_version or 0),
        "server_modified": doc.server_modified.isoformat() if doc.server_modified else None,
        "is_deleted": cint(doc.is_deleted or 0),
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
    }


@frappe.whitelist(allow_guest=False)
def list_bucket_templates(
    wallet_id: str,
    include_inactive: Optional[int] = 0,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    filters = _template_filters(wallet_id, active_only=not cint(include_inactive or 0))
    rows = frappe.get_all(
        "Hisabi Bucket Template",
        filters=filters,
        fields=["name"],
        order_by="is_default desc, modified desc",
    )

    templates = []
    for row in rows:
        doc = frappe.get_doc("Hisabi Bucket Template", row.name)
        templates.append(_serialize_template(doc))

    return {
        "templates": templates,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def get_default_bucket_template(wallet_id: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    name = frappe.get_value(
        "Hisabi Bucket Template",
        {
            "wallet_id": wallet_id,
            "is_deleted": 0,
            "is_active": 1,
            "is_default": 1,
        },
        "name",
    )
    if not name:
        return {"template": None, "server_time": now_datetime().isoformat()}

    doc = frappe.get_doc("Hisabi Bucket Template", name)
    return {
        "template": _serialize_template(doc),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def create_bucket_template(
    wallet_id: str,
    title: str,
    template_items: Any,
    is_default: Optional[int] = 0,
    is_active: Optional[int] = 1,
    client_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    doc = frappe.new_doc("Hisabi Bucket Template")
    doc.user = user
    doc.wallet_id = wallet_id
    doc.client_id = client_id or f"bucket-template-{frappe.generate_hash(length=12)}"
    doc.title = (title or "").strip()
    doc.is_default = cint(is_default or 0)
    doc.is_active = cint(is_active if is_active not in (None, "") else 1)
    doc.template_items = []

    for row in _parse_template_items(template_items):
        doc.append("template_items", row)

    apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
    doc.save(ignore_permissions=True)
    return {"template": _serialize_template(doc)}


@frappe.whitelist(allow_guest=False)
def update_bucket_template(
    template_id: str,
    wallet_id: str,
    title: Optional[str] = None,
    template_items: Any = None,
    is_default: Optional[int] = None,
    is_active: Optional[int] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    doc = _get_template_doc(template_id, wallet_id)

    if title is not None:
        doc.title = (title or "").strip()
    if is_default is not None:
        doc.is_default = cint(is_default)
    if is_active is not None:
        doc.is_active = cint(is_active)

    if template_items is not None:
        rows = _parse_template_items(template_items)
        doc.set("template_items", [])
        for row in rows:
            doc.append("template_items", row)

    apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
    doc.save(ignore_permissions=True)
    return {"template": _serialize_template(doc)}


@frappe.whitelist(allow_guest=False)
def delete_bucket_template(
    template_id: str,
    wallet_id: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    doc = _get_template_doc(template_id, wallet_id)
    doc.is_default = 0
    doc.is_active = 0
    apply_common_sync_fields(doc, bump_version=True, mark_deleted=True)
    if doc.meta.has_field("deleted_at") and not doc.deleted_at:
        doc.deleted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"status": "ok", "template_id": doc.name}
