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
