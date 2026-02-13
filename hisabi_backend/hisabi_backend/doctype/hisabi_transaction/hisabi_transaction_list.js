frappe.listview_settings["Hisabi Transaction"] = {
	add_fields: [
		"date_time",
		"transaction_type",
		"amount",
		"currency",
		"account",
		"category",
		"is_deleted",
	],
	filters: [["is_deleted", "=", 0]],
	order_by: "date_time desc",
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.transaction_type === "income") return [__("Income"), "green", "transaction_type,=,income"];
		if (doc.transaction_type === "transfer") return [__("Transfer"), "blue", "transaction_type,=,transfer"];
		return [__("Expense"), "orange", "transaction_type,=,expense"];
	},
};
