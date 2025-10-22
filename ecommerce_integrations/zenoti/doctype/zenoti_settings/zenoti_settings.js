// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on("Zenoti Settings", {
	setup: function (frm) {
		frm.set_query(
			"liability_income_account_for_gift_and_prepaid_cards",
			function () {
				if (!frm.doc.company) {
					frappe.throw(__("Please select company first"));
				}
				return {
					filters: {
						root_type: "Liability",
						is_group: 0,
						account_type: "Income Account",
						company: frm.doc.company,
					},
				};
			}
		);

		frm.set_query("default_purchase_warehouse", function () {
			if (!frm.doc.company) {
				frappe.throw(__("Please select company first"));
			}
			return {
				filters: {
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});

		frm.set_query("default_buying_price_list", function () {
			return {
				filters: {
					buying: 1,
				},
			};
		});

		frm.set_query("default_selling_price_list", function () {
			return {
				filters: {
					selling: 1,
				},
			};
		});
	},
	refresh(frm) {
		if (cint(frm.doc.enable_zenoti)) {
			frm.add_custom_button(__("Update Centers"), function () {
				frappe.call({
					method: "ecommerce_integrations.zenoti.doctype.zenoti_settings.zenoti_settings.update_centers",
					freeze: true,
					freeze_message: __("Updating Centers..."),
					callback: function (r) {
						if (!r.exc) {
							frappe.show_alert(__("Centers Updated"));
						}
					},
				});
			});
		}
	},
});
