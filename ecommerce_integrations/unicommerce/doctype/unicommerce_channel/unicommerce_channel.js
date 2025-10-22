// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on("Unicommerce Channel", {
	onload: function (frm) {
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});

		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));

		["warehouse", "return_warehouse"].forEach((wh_field) =>
			frm.set_query(wh_field, () => ({
				filters: {
					company: frm.doc.company,
					is_group: 0,
					disabled: 0,
				},
			}))
		);

		const tax_accounts = [
			"igst_account",
			"cgst_account",
			"sgst_account",
			"ugst_account",
			"tcs_account",
		];

		const misc_accounts = [
			"fnf_account",
			"cod_account",
			"gift_wrap_account",
		];

		tax_accounts.forEach((field_name) => {
			frm.set_query(field_name, () => ({
				query: "erpnext.controllers.queries.tax_account_query",
				filters: {
					account_type: ["Tax"],
					company: frm.doc.company,
				},
			}));
		});

		misc_accounts.forEach((field_name) => {
			frm.set_query(field_name, () => ({
				query: "erpnext.controllers.queries.tax_account_query",
				filters: {
					account_type: ["Chargeable", "Expense Account"],
					company: frm.doc.company,
				},
			}));
		});

		frm.set_query("cash_or_bank_account", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				root_type: "Asset",
				account_type: ["in", ["Cash", "Bank"]],
			},
		}));
	},
});
