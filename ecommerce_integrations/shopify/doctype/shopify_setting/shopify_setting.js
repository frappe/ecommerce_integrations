// Copyright (c) 2021, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shopify Setting", {
	onload: function (frm) {
		frappe.call({
			method:
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});
	},
});
