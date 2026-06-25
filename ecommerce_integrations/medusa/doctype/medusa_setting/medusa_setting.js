// Copyright (c) 2024, Frappe and contributors
// For license information, please see LICENSE

frappe.provide("ecommerce_integrations.medusa.medusa_setting");

frappe.ui.form.on("Medusa Setting", {
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

	fetch_medusa_locations: function (frm) {
		frappe.call({
			doc: frm.doc,
			method: "fetch_medusa_locations",
			callback: (r) => {
				if (!r.exc) refresh_field("medusa_warehouse_mapping");
			},
		});
	},

	refresh: function (frm) {
		frm.add_custom_button(__("Import Products"), function () {
			frappe.set_route("medusa-import-products");
		});
		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {
				integration: "medusa",
			});
		});

		// Show the webhook callback URL so the operator can wire up the subscriber.
		const site = frappe.urllib.get_base_url().replace(/^https?:\/\//, "");
		frm.get_field("webhook_help").$wrapper.find("code").remove();
		frm
			.get_field("webhook_help")
			.$wrapper.append(
				`<p><b>${__("Callback URL")}:</b> <code>https://${site}/api/method/ecommerce_integrations.medusa.connection.store_request_data</code></p>`,
			);

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
		frm.set_query("erpnext_warehouse", "medusa_warehouse_mapping", warehouse_query);

		frm.set_query("selling_price_list", () => {
			return { filters: { selling: 1 } };
		});

		frm.set_query("cost_center", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: "No",
				},
			};
		});

		frm.set_query("cash_bank_account", () => {
			return {
				filters: [
					["Account", "account_type", "in", ["Cash", "Bank"]],
					["Account", "root_type", "=", "Asset"],
					["Account", "is_group", "=", 0],
					["Account", "company", "=", frm.doc.company],
				],
			};
		});

		const tax_query = () => {
			return {
				query: "erpnext.controllers.queries.tax_account_query",
				filters: {
					account_type: ["Tax", "Chargeable", "Expense Account"],
					company: frm.doc.company,
				},
			};
		};

		frm.set_query("tax_account", "taxes", tax_query);
		frm.set_query("default_sales_tax_account", tax_query);
		frm.set_query("default_shipping_charges_account", tax_query);
	},
});
