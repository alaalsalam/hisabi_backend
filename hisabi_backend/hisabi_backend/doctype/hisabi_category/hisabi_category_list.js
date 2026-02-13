frappe.listview_settings["Hisabi Category"] = {
	add_fields: ["category_name", "kind", "parent_category", "archived", "sort_order", "is_deleted"],
	filters: [["is_deleted", "=", 0], ["archived", "=", 0]],
	order_by: "sort_order asc, category_name asc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.archived) return [__("Archived"), "gray", "archived,=,1"];
		if (doc.kind === "income") return [__("Income"), "green", "kind,=,income"];
		return [__("Expense"), "orange", "kind,=,expense"];
	},
};
