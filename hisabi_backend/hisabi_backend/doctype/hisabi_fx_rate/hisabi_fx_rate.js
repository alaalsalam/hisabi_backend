frappe.ui.form.on("Hisabi FX Rate", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.wallet_id) return;
		if (!(frappe.user.has_role("System Manager") || frappe.user.has_role("Hisabi Admin"))) return;

		frm.add_custom_button(__("Seed Default Rates"), () => {
			const defaultBase = (frm.doc.base_currency || "SAR").toUpperCase();
			frappe.prompt(
				[
					{
						fieldname: "base_currency",
						fieldtype: "Data",
						label: __("Base Currency"),
						default: defaultBase,
						reqd: 1,
					},
					{
						fieldname: "enabled_currencies",
						fieldtype: "Data",
						label: __("Enabled Currencies (comma-separated)"),
						default: "SAR,USD,YER",
					},
					{
						fieldname: "overwrite_defaults",
						fieldtype: "Check",
						label: __("Overwrite Existing Default Rows"),
						default: 0,
					},
				],
				(values) => {
					frappe.call({
						method:
							"hisabi_backend.hisabi_backend.doctype.hisabi_fx_rate.hisabi_fx_rate.seed_default_rates_for_wallet",
						args: {
							wallet_id: frm.doc.wallet_id,
							base_currency: values.base_currency,
							enabled_currencies: values.enabled_currencies,
							overwrite_defaults: values.overwrite_defaults ? 1 : 0,
						},
						freeze: true,
						freeze_message: __("Seeding default FX rates..."),
						callback: (r) => {
							const seed = (r && r.message && r.message.seed) || {};
							frappe.show_alert({
								message: __(
									"Default FX seeded: inserted {0}, updated {1}, skipped {2}",
									[
										seed.inserted || 0,
										seed.updated || 0,
										seed.skipped || 0,
									]
								),
								indicator: "green",
							});
							frm.reload_doc();
						},
					});
				},
				__("Seed Default FX Rates"),
				__("Apply")
			);
		});
	},
});
