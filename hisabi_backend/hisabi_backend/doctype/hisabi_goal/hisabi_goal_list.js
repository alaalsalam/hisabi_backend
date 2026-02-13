frappe.listview_settings["Hisabi Goal"] = {
	add_fields: ["goal_name", "goal_type", "target_amount", "current_amount", "status", "target_date", "is_deleted"],
	filters: [["is_deleted", "=", 0]],
	order_by: "target_date asc, goal_name asc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.status === "completed") return [__("Completed"), "green", "status,=,completed"];
		if (doc.status === "cancelled") return [__("Cancelled"), "gray", "status,=,cancelled"];
		return [__("Active"), "blue", "status,=,active"];
	},
};
