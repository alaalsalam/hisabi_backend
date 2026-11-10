"""The React SPA is served by the www page `hisabi-wallet`; its client routes must resolve to it on a
full page load (deep link / reload / PWA first-install reload), and must not shadow Frappe routes."""

import re
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase as FrappeTestCase
from frappe.website.path_resolver import resolve_path

from hisabi_backend import hooks

APP_TSX = Path(frappe.get_app_path("hisabi_backend")).parent / "hisabi-your-smart-wallet" / "src" / "App.tsx"


class TestSpaWebsiteRoutes(FrappeTestCase):
    def setUp(self):
        # Frappe only evaluates dynamic route rules inside a request environment.
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request

        self._prev_request = getattr(frappe.local, "request", None)
        frappe.local.request = Request(EnvironBuilder(path="/", base_url="http://localhost").get_environ())
        frappe.cache.delete_value("website_route_rules")

    def tearDown(self):
        frappe.local.request = self._prev_request

    def test_home_page_is_spa(self):
        self.assertEqual(resolve_path(""), "hisabi-wallet")

    def test_spa_routes_and_subpaths_resolve_to_spa_page(self):
        for route in ("auth", "ledger", "settings", "wallets"):
            self.assertEqual(resolve_path(route), "hisabi-wallet", route)
            self.assertEqual(resolve_path(f"{route}/some/child"), "hisabi-wallet", route)

    def test_frappe_routes_are_not_shadowed(self):
        for route in ("login", "app", "update-password", "me"):
            self.assertNotEqual(resolve_path(route), "hisabi-wallet", route)

    def test_rules_cover_every_top_level_route_in_app_tsx(self):
        if not APP_TSX.exists():
            self.skipTest("frontend source not present")
        paths = re.findall(r'<Route path="/([^"/*]+)', APP_TSX.read_text())
        missing = sorted({p for p in paths if p != "hisabi-wallet"} - set(hooks._SPA_ROUTES))
        self.assertEqual(missing, [], "add these App.tsx routes to hooks._SPA_ROUTES")
