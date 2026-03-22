frappe.ui.form.on("Hisabi AI Settings", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.set_intro(__("احفظ مزود الذكاء الاصطناعي المفضل وسيتم مزامنته تلقائيًا مع wallet.yemenfrappe.com"), "blue");
		}
	},
});
