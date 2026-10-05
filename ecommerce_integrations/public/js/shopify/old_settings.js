frappe.ui.form.on("Shopify Settings", {
	onload_post_render: function (frm) {
		let msg = __("You have Ecommerce Integration app installed.") + " ";
		msg += __("This setting page refers to old Shopify connector.");
		frappe.msgprint(msg);
	},
});
