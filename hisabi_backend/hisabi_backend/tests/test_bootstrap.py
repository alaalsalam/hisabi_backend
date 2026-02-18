import os
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.tests import bootstrap


class TestBootstrap(FrappeTestCase):
	def test_bootstrap_does_not_run_in_production(self):
		with (
			patch("hisabi_backend.tests.bootstrap.frappe.flags", SimpleNamespace(in_test=False)),
			patch.dict(os.environ, {"FRAPPE_ENV": "production"}, clear=False),
			patch("hisabi_backend.tests.bootstrap.ensure_all_departments") as ensure_all_departments,
			patch("hisabi_backend.tests.bootstrap.ensure_test_encryption_key") as ensure_test_encryption_key,
		):
			bootstrap.run_test_bootstrap()

		ensure_all_departments.assert_not_called()
		ensure_test_encryption_key.assert_not_called()
