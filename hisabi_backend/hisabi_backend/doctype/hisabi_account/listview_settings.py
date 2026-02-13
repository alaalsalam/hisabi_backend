from frappe import _


def get_listview_settings():
    return {
        "title_field": "account_name",
        "search_fields": ["account_name", "account_type", "currency", "client_id"],
        "columns": [
            {"fieldname": "account_name", "label": _("Account"), "width": 220},
            {"fieldname": "account_type", "label": _("Type"), "width": 110},
            {"fieldname": "currency", "label": _("Currency"), "width": 90},
            {"fieldname": "current_balance", "label": _("Balance"), "width": 120},
            {"fieldname": "archived", "label": _("Archived"), "width": 90},
        ],
        "filters": [["archived", "=", 0], ["is_deleted", "=", 0]],
        "order_by": "sort_order asc, account_name asc",
    }
