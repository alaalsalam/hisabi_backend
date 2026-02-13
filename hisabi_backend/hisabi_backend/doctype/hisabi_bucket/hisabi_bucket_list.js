frappe.listview_settings["Hisabi Bucket"] = {
	add_fields: ["bucket_name", "title", "is_active", "archived", "sort_order", "is_deleted"],
	filters: [["is_deleted", "=", 0], ["archived", "=", 0]],
	order_by: "sort_order asc, bucket_name asc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.archived) return [__("Archived"), "gray", "archived,=,1"];
		if (doc.is_active) return [__("Active"), "green", "is_active,=,1"];
		return [__("Inactive"), "orange", "is_active,=,0"];
	},
};
