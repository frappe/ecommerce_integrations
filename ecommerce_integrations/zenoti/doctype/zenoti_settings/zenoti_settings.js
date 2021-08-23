// Copyright (c) 2021, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Zenoti Settings', {
	setup: function(frm){
		frm.set_query("default_liability_account", function() {
			return {
				filters: {
					root_type: "Liability",
					is_group: 0
				}
			}
		});

		frm.set_query("default_buying_price_list", function() {
			return {
				filters: {
					buying: 1,
				}
			}
		});

		frm.set_query("default_selling_price_list", function() {
			return {
				filters: {
					selling: 1,
				}
			}
		});

		frm.set_query("erpnext_warehouse", "map_zenoti_centre_to_erpnext_cost_center_and_warehouse", function() {
			return {
				filters: {
					is_group: 0
				}
			};
		});

		frm.set_query("erpnext_cost_center", "map_zenoti_centre_to_erpnext_cost_center_and_warehouse", function() {
			return {
				filters: {
					is_group: 0
				}
			};
		});
	}
});
