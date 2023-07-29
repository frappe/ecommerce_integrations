// Copyright (c) 2022, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon SP API Settings', {
	refresh(frm) {
		frm.trigger("set_queries");
		frm.set_df_property("amazon_fields_map", "cannot_add_rows", true);
		frm.set_df_property("amazon_fields_map", "cannot_delete_rows", true);
	},

	set_queries(frm) {
		frm.set_query("warehouse", () => {
			return {
				filters: {
					"is_group": 0,
					"company": frm.doc.company,
				}
			};
		});

		frm.set_query("market_place_account_group", () => {
			return {
				filters: {
					"is_group": 1,
					"company": frm.doc.company,
				}
			};
		});
	}
});
