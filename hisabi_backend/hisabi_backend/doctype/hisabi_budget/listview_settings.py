from frappe import _


def get_listview_settings():
    return {
        "title_field": "budget_name",
        "search_fields": ["budget_name", "period", "scope_type", "category", "currency"],
        "columns": [
            {"fieldname": "budget_name", "label": _("Budget"), "width": 220},
            {"fieldname": "period", "label": _("Period"), "width": 110},
            {"fieldname": "amount_base", "label": _("Amount"), "width": 120},
            {"fieldname": "spent_amount", "label": _("Spent"), "width": 120},
            {"fieldname": "archived", "label": _("Archived"), "width": 90},
        ],
        "filters": [["archived", "=", 0], ["is_deleted", "=", 0]],
        "order_by": "start_date desc, budget_name asc",
    }
