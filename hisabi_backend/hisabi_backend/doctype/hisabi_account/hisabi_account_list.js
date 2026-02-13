frappe.listview_settings["Hisabi Account"] = {
	add_fields: ["account_name", "account_type", "currency", "current_balance", "archived", "sort_order", "is_deleted"],
	filters: [["is_deleted", "=", 0], ["archived", "=", 0]],
	order_by: "sort_order asc, account_name asc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.archived) return [__("Archived"), "gray", "archived,=,1"];
		return [__("Active"), "green", "archived,=,0"];
	},
};
