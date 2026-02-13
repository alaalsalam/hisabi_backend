from frappe import _


def get_listview_settings():
    return {
        "title_field": "category_name",
        "search_fields": ["category_name", "kind", "parent_category", "client_id"],
        "columns": [
            {"fieldname": "category_name", "label": _("Category"), "width": 220},
            {"fieldname": "kind", "label": _("Kind"), "width": 100},
            {"fieldname": "parent_category", "label": _("Parent"), "width": 180},
            {"fieldname": "archived", "label": _("Archived"), "width": 90},
        ],
        "filters": [["archived", "=", 0], ["is_deleted", "=", 0]],
        "order_by": "sort_order asc, category_name asc",
    }
