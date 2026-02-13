frappe.listview_settings["Hisabi Budget"] = {
	add_fields: ["budget_name", "period", "amount_base", "spent_amount", "archived", "start_date", "is_deleted"],
	filters: [["is_deleted", "=", 0], ["archived", "=", 0]],
	order_by: "start_date desc, budget_name asc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.archived) return [__("Archived"), "gray", "archived,=,1"];
		return [__("Active"), "green", "archived,=,0"];
	},
};
