from frappe import _


def get_listview_settings():
    return {
        "title_field": "bucket_name",
        "search_fields": ["bucket_name", "title", "client_id", "color"],
        "columns": [
            {"fieldname": "bucket_name", "label": _("Bucket"), "width": 220},
            {"fieldname": "is_active", "label": _("Active"), "width": 80},
            {"fieldname": "archived", "label": _("Archived"), "width": 90},
            {"fieldname": "sort_order", "label": _("Sort"), "width": 80},
        ],
        "filters": [["archived", "=", 0], ["is_deleted", "=", 0]],
        "order_by": "sort_order asc, bucket_name asc",
    }
