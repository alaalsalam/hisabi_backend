from frappe import _


def get_listview_settings():
    return {
        "title_field": "goal_name",
        "search_fields": ["goal_name", "goal_type", "status", "currency", "client_id"],
        "columns": [
            {"fieldname": "goal_name", "label": _("Goal"), "width": 220},
            {"fieldname": "goal_type", "label": _("Type"), "width": 110},
            {"fieldname": "target_amount", "label": _("Target"), "width": 120},
            {"fieldname": "current_amount", "label": _("Current"), "width": 120},
            {"fieldname": "status", "label": _("Status"), "width": 100},
        ],
        "filters": [["is_deleted", "=", 0]],
        "order_by": "target_date asc, goal_name asc",
    }
