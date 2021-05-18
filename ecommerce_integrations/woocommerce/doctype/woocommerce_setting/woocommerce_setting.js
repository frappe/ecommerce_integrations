// Copyright (c) 2021, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Woocommerce Setting', {
	refresh (frm) {
		frm.trigger("add_button_generate_secret");
		frm.trigger("check_enabled");
		frm.set_query("tax_account", ()=>{
			return {
				"filters": {
					"company": frappe.defaults.get_default("company"),
					"is_group": 0
				}
			};
		});
	},

	enable_sync (frm) {
		frm.trigger("check_enabled");
	},

	add_button_generate_secret(frm) {
		frm.add_custom_button(__('Generate Secret'), () => {
			frappe.confirm(
				__("Apps using current key won't be able to access, are you sure?"),
				() => {
					frappe.call({
						type:"POST",
						method:"ecommerce_integrations.woocommerce.doctype.woocommerce_setting.woocommerce_setting.generate_secret",
					}).done(() => {
						frm.reload_doc();
					}).fail(() => {
						frappe.msgprint(__("Could not generate Secret"));
					});
				}
			);
		});
	},

	check_enabled (frm) {
		frm.set_df_property("woocommerce_server_url", "reqd", frm.doc.enable_sync);
		frm.set_df_property("api_consumer_key", "reqd", frm.doc.enable_sync);
		frm.set_df_property("api_consumer_secret", "reqd", frm.doc.enable_sync);
	}
});

frappe.ui.form.on("Woocommerce Setting", "onload", function () {
	frappe.call({
		method: "ecommerce_integrations.woocommerce.doctype.woocommerce_setting.woocommerce_setting.get_series",
		callback: function (r) {
			$.each(r.message, function (key, value) {
				set_field_options(key, value);
			});
		}
	});
});
