frappe.listview_settings["Hisabi Settings"] = {
	add_fields: ["user_name", "base_currency", "locale", "enforce_fx", "wallet_id", "is_deleted", "server_modified"],
	filters: [["is_deleted", "=", 0]],
	order_by: "server_modified desc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.enforce_fx) return [__("FX Enforced"), "orange", "enforce_fx,=,1"];
		return [__("Standard"), "blue", "enforce_fx,=,0"];
	},
};
