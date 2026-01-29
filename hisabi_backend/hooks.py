app_name = "hisabi_backend"
app_title = "Hisabi Backend"
app_publisher = "alaalsalam"
app_description = "Backend App for Hisabi app "
app_email = "alaalsalam101@gmail.com"
app_license = "mit"

# Export hisabi workspace for consistent deploy
fixtures = [
	{"doctype": "Workspace", "filters": [["name", "=", "Hisabi"]]}
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "hisabi_backend",
# 		"logo": "/assets/hisabi_backend/logo.png",
# 		"title": "Hisabi Backend",
# 		"route": "/hisabi_backend",
# 		"has_permission": "hisabi_backend.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/hisabi_backend/css/hisabi_backend.css"
# app_include_js = "/assets/hisabi_backend/js/hisabi_backend.js"

# include js, css files in header of web template
# web_include_css = "/assets/hisabi_backend/css/hisabi_backend.css"
# web_include_js = "/assets/hisabi_backend/js/hisabi_backend.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "hisabi_backend/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "hisabi_backend/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "hisabi_backend.utils.jinja_methods",
# 	"filters": "hisabi_backend.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "hisabi_backend.install.before_install"
after_install = "hisabi_backend.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "hisabi_backend.uninstall.before_uninstall"
# after_uninstall = "hisabi_backend.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "hisabi_backend.utils.before_app_install"
# after_app_install = "hisabi_backend.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "hisabi_backend.utils.before_app_uninstall"
# after_app_uninstall = "hisabi_backend.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "hisabi_backend.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

doc_events = {
	"User": {"validate": "hisabi_backend.utils.user_events.validate_user_phone"},
	"Hisabi Account": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Category": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Transaction": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Bucket": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Allocation Rule": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Allocation Rule Line": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Transaction Allocation": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Budget": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Goal": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Debt": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Debt Installment": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Debt Request": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Jameya": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Jameya Payment": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi FX Rate": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Custom Currency": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Attachment": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
	"Hisabi Audit Log": {"validate": "hisabi_backend.utils.wallet_doc_events.validate_wallet_scope"},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"hisabi_backend.tasks.all"
# 	],
# 	"daily": [
# 		"hisabi_backend.tasks.daily"
# 	],
# 	"hourly": [
# 		"hisabi_backend.tasks.hourly"
# 	],
# 	"weekly": [
# 		"hisabi_backend.tasks.weekly"
# 	],
# 	"monthly": [
# 		"hisabi_backend.tasks.monthly"
# 	],
# }

# Testing
# -------

before_tests = "hisabi_backend.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "hisabi_backend.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "hisabi_backend.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["hisabi_backend.utils.before_request"]
# after_request = ["hisabi_backend.utils.after_request"]

# Job Events
# ----------
# before_job = ["hisabi_backend.utils.before_job"]
# after_job = ["hisabi_backend.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"hisabi_backend.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
