from frappe import _


def get_listview_settings():
    return {
        "title_field": "wallet_name",
        "search_fields": ["wallet_name", "client_id", "owner_user", "status"],
        "columns": [
            {"fieldname": "wallet_name", "label": _("Wallet"), "width": 220},
            {"fieldname": "status", "label": _("Status"), "width": 110},
            {"fieldname": "owner_user", "label": _("Owner"), "width": 220},
            {"fieldname": "doc_version", "label": _("Version"), "width": 90},
        ],
        "filters": [["is_deleted", "=", 0]],
        "order_by": "wallet_name asc",
    }
