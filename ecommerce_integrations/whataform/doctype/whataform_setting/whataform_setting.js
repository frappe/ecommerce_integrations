// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.provide("ecommerce_integrations.whataform.whataform_setting");

frappe.ui.form.on("Whataform Setting", {
	onload: function (frm) {
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});
	},

	refresh: function (frm) {
		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {
				integration: "Whataform",
			});
		});
		frm.trigger("setup_queries");
	},

	setup_queries: function (frm) {
		const warehouse_query = () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					disabled: 0,
				},
			};
		};
		frm.set_query("warehouse", warehouse_query);
		frm.set_query(
			"erpnext_warehouse",
			"whataform_warehouse_mapping",
			warehouse_query
		);
	},
});
