frappe.ui.form.on("Hisabi User", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!frappe.user.has_role("System Manager") && !frappe.user.has_role("Hisabi Admin")) return;

		const isFrozen = (frm.doc.account_status || "Active") === "Frozen";
		if (isFrozen) {
			frm.add_custom_button(__("Unfreeze Account"), async () => {
				await frappe.call({
					method: "hisabi_backend.hisabi_backend.doctype.hisabi_user.hisabi_user.unfreeze_account",
					args: { docname: frm.doc.name },
					freeze: true,
					freeze_message: __("Unfreezing account..."),
				});
				await frm.reload_doc();
				frappe.show_alert({ message: __("Account unfrozen"), indicator: "green" });
			});
		} else {
			frm.add_custom_button(__("Freeze Account"), () => {
				frappe.prompt(
					[
						{
							fieldtype: "Small Text",
							fieldname: "reason",
							label: __("Reason"),
							reqd: 0,
						},
					],
					async (values) => {
						await frappe.call({
							method: "hisabi_backend.hisabi_backend.doctype.hisabi_user.hisabi_user.freeze_account",
							args: { docname: frm.doc.name, reason: values.reason || "" },
							freeze: true,
							freeze_message: __("Freezing account..."),
						});
						await frm.reload_doc();
						frappe.show_alert({ message: __("Account frozen"), indicator: "orange" });
					},
					__("Freeze User Account"),
					__("Confirm")
				);
			});
		}

		frm.page.add_menu_item(__("Delete Account And All Related Data"), () => {
			frappe.confirm(
				__(
					"This will permanently delete the user account and all related Hisabi data (wallets, transactions, devices, settings). Continue?"
				),
				() => {
					frappe.call({
						method: "hisabi_backend.hisabi_backend.doctype.hisabi_user.hisabi_user.delete_account",
						args: { docname: frm.doc.name, delete_frappe_user: 1 },
						freeze: true,
						freeze_message: __("Deleting account and related data..."),
					}).then(() => {
						frappe.show_alert({ message: __("Account deleted"), indicator: "red" });
						frappe.set_route("List", "Hisabi User");
					});
				}
			);
		});
	},
});
