from frappe import _


def get_listview_settings():
    return {
        "title_field": "client_id",
        "search_fields": ["client_id", "note", "account", "category", "transaction_type"],
        "columns": [
            {"fieldname": "date_time", "label": _("Date"), "width": 170},
            {"fieldname": "transaction_type", "label": _("Type"), "width": 100},
            {"fieldname": "amount", "label": _("Amount"), "width": 120},
            {"fieldname": "currency", "label": _("Currency"), "width": 90},
            {"fieldname": "account", "label": _("Account"), "width": 180},
            {"fieldname": "category", "label": _("Category"), "width": 180},
        ],
        "filters": [["is_deleted", "=", 0]],
        "order_by": "date_time desc",
    }
