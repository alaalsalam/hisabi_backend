import hisabi_backend
from frappe.tests.utils import FrappeTestCase

from hisabi_backend.api.v1.health import diag


class TestHealthDiag(FrappeTestCase):
    def test_diag_uses_package_version(self):
        payload = diag()
        app = payload.get("app") or {}
        self.assertEqual(app.get("version"), getattr(hisabi_backend, "__version__", None))
        self.assertIn("commit", app)
        self.assertIn("encryption_key_present", payload)
