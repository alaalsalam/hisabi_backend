"""DocType controller for Hisabi Bucket Template."""

from __future__ import annotations

from typing import List

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from hisabi_backend.utils.bucket_allocations import PERCENT_EPSILON
from hisabi_backend.utils.sync_common import apply_common_sync_fields


class HisabiBucketTemplate(Document):
    def before_insert(self):
        if not self.user:
            self.user = frappe.session.user
        if not self.client_id:
            self.client_id = f"bucket-template-{frappe.generate_hash(length=12)}"

    def validate(self):
        self.title = (self.title or "").strip()
        if not self.title:
            frappe.throw(_("title is required"), frappe.ValidationError)

        if not self.client_id:
            self.client_id = f"bucket-template-{frappe.generate_hash(length=12)}"

        self._normalize_flags()
        self._normalize_items()

        if cint(self.is_deleted):
            self.is_default = 0
            return

        if cint(self.is_default) and not cint(self.is_active):
            frappe.throw(_("Default template must be active"), frappe.ValidationError)

        self._validate_template_items()
        self._ensure_single_default()

    def _normalize_flags(self) -> None:
        self.is_default = cint(self.is_default or 0)
        self.is_active = cint(self.is_active if self.is_active not in (None, "") else 1)

    def _normalize_items(self) -> None:
        rows = self.get("template_items") or []
        for row in rows:
            bucket_id = (row.get("bucket_id") or row.get("bucket") or "").strip()
            row.bucket_id = bucket_id
            row.percentage = flt(row.get("percentage") or row.get("percent") or 0, 6)

    def _validate_template_items(self) -> None:
        rows = self.get("template_items") or []
        if not rows:
            frappe.throw(_("template_items is required"), frappe.ValidationError)

        seen = set()
        bucket_ids: List[str] = []
        total = 0.0

        for idx, row in enumerate(rows, start=1):
            bucket_id = (row.get("bucket_id") or "").strip()
            if not bucket_id:
                frappe.throw(_("Bucket is required in template row #{0}").format(idx), frappe.ValidationError)
            if bucket_id in seen:
                frappe.throw(_("Duplicate bucket in template items"), frappe.ValidationError)
            seen.add(bucket_id)
            bucket_ids.append(bucket_id)

            percentage = flt(row.get("percentage"), 6)
            if percentage <= 0 or percentage > 100:
                frappe.throw(_("Percentage must be between 0 and 100 in template row #{0}").format(idx), frappe.ValidationError)
            total += percentage

        if abs(total - 100.0) > PERCENT_EPSILON:
            frappe.throw(_("Template percentages must sum to 100"), frappe.ValidationError)

        buckets = frappe.get_all(
            "Hisabi Bucket",
            filters={
                "name": ["in", sorted(bucket_ids)],
                "wallet_id": self.wallet_id,
                "is_deleted": 0,
            },
            fields=["name", "is_active", "archived"],
        )
        if len(buckets) != len(bucket_ids):
            frappe.throw(_("Template buckets must belong to the same wallet"), frappe.PermissionError)

        by_name = {row.name: row for row in buckets}
        for bucket_id in bucket_ids:
            bucket = by_name.get(bucket_id)
            if not bucket:
                frappe.throw(_("Bucket does not exist in this wallet"), frappe.PermissionError)
            is_active = bucket.get("is_active")
            if is_active in (None, ""):
                is_active = 0 if cint(bucket.get("archived") or 0) else 1
            if cint(is_active) == 0:
                frappe.throw(_("Inactive bucket cannot be used in template"), frappe.ValidationError)

    def _ensure_single_default(self) -> None:
        if not cint(self.is_default) or not self.wallet_id:
            return

        others = frappe.get_all(
            "Hisabi Bucket Template",
            filters={
                "wallet_id": self.wallet_id,
                "is_default": 1,
                "is_deleted": 0,
                "name": ["!=", self.name],
            },
            pluck="name",
        )
        for name in others:
            other_doc = frappe.get_doc("Hisabi Bucket Template", name)
            if cint(other_doc.is_default or 0) != 1:
                continue
            other_doc.is_default = 0
            apply_common_sync_fields(other_doc, bump_version=True, mark_deleted=bool(other_doc.is_deleted))
            other_doc.save(ignore_permissions=True)
