frappe.listview_settings["Hisabi Wallet"] = {
	add_fields: ["wallet_name", "status", "owner_user", "is_deleted", "doc_version"],
	filters: [["is_deleted", "=", 0]],
	order_by: "wallet_name asc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.status === "archived") return [__("Archived"), "gray", "status,=,archived"];
		return [__("Active"), "green", "status,=,active"];
	},
};
