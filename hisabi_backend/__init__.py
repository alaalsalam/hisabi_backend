import os

__version__ = "v1.0.0-rc.1"

# expose nested package content so frappe imports (doctype, api, utils) resolve
_package_dir = os.path.join(os.path.dirname(__file__), "hisabi_backend")
if _package_dir not in __path__:
	__path__.append(_package_dir)
