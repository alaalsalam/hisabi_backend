from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from hisabi_backend.utils.validators import validate_client_id


INVITE_CODE_RE = re.compile(r"^[A-Z0-9]{8,12}$")


class HisabiWalletInvite(Document):
    def validate(self) -> None:
        if self.client_id:
            self.client_id = validate_client_id(self.client_id)
        if self.name and self.client_id and self.name != self.client_id:
            self.name = self.client_id

        if self.invite_code:
            self.invite_code = self.invite_code.strip().upper()
            if not INVITE_CODE_RE.match(self.invite_code):
                frappe.throw(_("Invalid invite_code"), frappe.ValidationError)

        if self.status == "expired" and not self.expires_at:
            self.expires_at = now_datetime()
