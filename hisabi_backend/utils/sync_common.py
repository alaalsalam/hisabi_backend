"""Common sync helpers used across doctypes."""

from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe.utils import cint, now_datetime

from hisabi_backend.utils.validators import validate_client_id

COMMON_SYNC_FIELDS = (
    "client_id",
    "client_created_ms",
    "client_modified_ms",
    "doc_version",
    "server_modified",
    "is_deleted",
    "deleted_at",
)


def map_common_sync_fields(doc: frappe.model.document.Document, payload: Optional[Dict[str, Any]]) -> None:
    """Map common sync fields from payload onto a document.

    Only fields present on the DocType are set.
    """
    if not payload:
        return

    if "client_id" in payload:
        validate_client_id(payload.get("client_id"))

    for field in ("client_id", "client_created_ms", "client_modified_ms"):
        if field in payload and doc.meta.has_field(field):
            doc.set(field, payload.get(field))


def bump_doc_version(doc: frappe.model.document.Document) -> None:
    """Increment doc_version if available on the DocType."""
    if doc.meta.has_field("doc_version"):
        doc.doc_version = cint(doc.doc_version) + 1


def set_server_modified(doc: frappe.model.document.Document) -> None:
    """Set server_modified to current Datetime if available."""
    if doc.meta.has_field("server_modified"):
        doc.server_modified = now_datetime()


def apply_soft_delete(
    doc: frappe.model.document.Document, *, is_deleted: bool, deleted_at: Optional[str] = None
) -> None:
    """Apply soft delete fields if present on the DocType."""
    if doc.meta.has_field("is_deleted"):
        doc.is_deleted = 1 if is_deleted else 0

    if doc.meta.has_field("deleted_at"):
        doc.deleted_at = deleted_at or (now_datetime() if is_deleted else None)


def apply_common_sync_fields(
    doc: frappe.model.document.Document,
    payload: Optional[Dict[str, Any]] = None,
    *,
    bump_version: bool = True,
    mark_deleted: bool = False,
) -> None:
    """Apply common sync fields in a single call.

    - maps common payload fields
    - increments doc_version
    - sets server_modified
    - applies soft delete fields
    """
    map_common_sync_fields(doc, payload)

    if bump_version:
        bump_doc_version(doc)

    set_server_modified(doc)
    apply_soft_delete(doc, is_deleted=mark_deleted)
