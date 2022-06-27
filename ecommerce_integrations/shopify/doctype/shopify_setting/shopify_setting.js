// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.provide("ecommerce_integrations.shopify.shopify_setting");

frappe.ui.form.on("Shopify Setting", {
	onload: function (frm) {
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});

		ecommerce_integrations.shopify.shopify_setting.setup_queries(frm);
	},

	fetch_shopify_locations: function (frm) {
		frappe.call({
			doc: frm.doc,
			method: "update_location_table",
			callback: (r) => {
				if (!r.exc) refresh_field("shopify_warehouse_mapping");
			},
		});
	},

	refresh: function (frm) {
		frm.add_custom_button(__('Import Products'), function () {
			frappe.set_route('shopify-import-products');
		});
		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {"integration": "Shopify"});
		});
	}
});

$.extend(ecommerce_integrations.shopify.shopify_setting, {
	setup_queries: function (frm) {
		frm.fields_dict["warehouse"].get_query = function (doc) {
			return {
				filters: {
					company: doc.company,
					is_group: "No",
				},
			};
		};

		frm.fields_dict["taxes"].grid.get_field(
			"tax_account"
		).get_query = function (doc) {
			return {
				query: "erpnext.controllers.queries.tax_account_query",
				filters: {
					account_type: ["Tax", "Chargeable", "Expense Account"],
					company: doc.company,
				},
			};
		};

		frm.fields_dict["cash_bank_account"].get_query = function (doc) {
			return {
				filters: [
					["Account", "account_type", "in", ["Cash", "Bank"]],
					["Account", "root_type", "=", "Asset"],
					["Account", "is_group", "=", 0],
					["Account", "company", "=", doc.company],
				],
			};
		};

		frm.fields_dict["cost_center"].get_query = function (doc) {
			return {
				filters: {
					company: doc.company,
					is_group: "No",
				},
			};
		};

		frm.fields_dict["price_list"].get_query = function () {
			return {
				filters: {
					selling: 1,
				},
			};
		};

		frm.fields_dict["shopify_warehouse_mapping"].grid.get_field(
			"erpnext_warehouse"
		).get_query = function (doc) {
			return {
				filters: {
					is_group: 0,
					company: doc.company,
					disabled: 0,
				},
			};
		};
	},
});
