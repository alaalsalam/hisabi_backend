from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from hisabi_backend.utils.sync_common import apply_common_sync_fields


class HisabiWalletMember(Document):
    def validate(self) -> None:
        # Normalize joined/removed timestamps.
        if self.status == "active" and not self.joined_at:
            self.joined_at = now_datetime()
        if self.status == "removed" and not self.removed_at:
            self.removed_at = now_datetime()

        apply_common_sync_fields(self, bump_version=False, mark_deleted=bool(self.is_deleted))
