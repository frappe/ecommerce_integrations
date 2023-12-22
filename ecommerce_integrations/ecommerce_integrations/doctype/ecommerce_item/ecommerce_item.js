// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on('Ecommerce Item', {
	refresh: function(frm) {
		frm.add_custom_button(__("Sync Item Handle"), () => {
			frappe.call({
				"method":"ecommerce_integrations.shopify.real_time_update.set_main_image_and_handle_in_erpnext",
                "args":{
                    "shopify_id":frm.doc.integration_item_code
                }
			})
		});
	}
});
