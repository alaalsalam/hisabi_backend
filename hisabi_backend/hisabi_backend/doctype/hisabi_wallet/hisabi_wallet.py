from __future__ import annotations

import frappe
from frappe.model.document import Document

from hisabi_backend.utils.sync_common import apply_common_sync_fields
from hisabi_backend.utils.validators import validate_client_id


class HisabiWallet(Document):
    def validate(self) -> None:
        # Enforce name == client_id
        if self.client_id:
            self.client_id = validate_client_id(self.client_id)
        if self.name and self.client_id and self.name != self.client_id:
            self.name = self.client_id

        if not self.owner_user:
            self.owner_user = frappe.session.user

        apply_common_sync_fields(self, bump_version=False, mark_deleted=bool(self.is_deleted))
