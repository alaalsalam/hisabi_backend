from frappe import _


def get_listview_settings():
    return {
        "title_field": "user_name",
        "search_fields": ["user_name", "user", "wallet_id", "base_currency", "locale"],
        "columns": [
            {"fieldname": "user_name", "label": _("User Name"), "width": 180},
            {"fieldname": "base_currency", "label": _("Base Currency"), "width": 120},
            {"fieldname": "locale", "label": _("Locale"), "width": 120},
            {"fieldname": "enforce_fx", "label": _("Enforce FX"), "width": 100},
            {"fieldname": "wallet_id", "label": _("Wallet"), "width": 220},
        ],
        "filters": [["is_deleted", "=", 0]],
        "order_by": "server_modified desc",
    }
