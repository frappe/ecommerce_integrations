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
		frm.add_custom_button(__("Import Products"), function () {
			frappe.set_route("shopify-import-products");
		});
		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {
				integration: "Shopify",
			});
		});
		frm.add_custom_button(__("Bulk Sync Order Metafields"), function () {
			show_bulk_sync_metafields_dialog();
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
			"shopify_warehouse_mapping",
			warehouse_query
		);

		frm.set_query("price_list", () => {
			return {
				filters: {
					selling: 1,
				},
			};
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

function show_bulk_sync_metafields_dialog() {
	const today = frappe.datetime.get_today();
	const month_start = frappe.datetime.month_start(today);

	const d = new frappe.ui.Dialog({
		title: __("Bulk Sync Shopify Order Metafields"),
		fields: [
			{
				fieldtype: "Date",
				fieldname: "from_date",
				label: __("From Date"),
				default: month_start,
				reqd: 1,
			},
			{
				fieldtype: "Date",
				fieldname: "to_date",
				label: __("To Date"),
				default: today,
				reqd: 1,
			},
		],
		primary_action_label: __("Sync"),
		primary_action: function (values) {
			d.hide();
			frappe.call({
				method: "ecommerce_integrations.shopify.order.bulk_sync_shopify_order_metafields",
				args: {
					from_date: values.from_date,
					to_date: values.to_date,
				},
				freeze: true,
				freeze_message: __("Syncing metafields for Sales Orders..."),
				callback: function (r) {
					if (r.exc) {
						frappe.msgprint({
							title: __("Error"),
							indicator: "red",
							message: r.exc,
						});
						return;
					}
					const data = r.message;
					if (!data.ok && data.message) {
						frappe.msgprint({
							title: __("Bulk Sync"),
							indicator: "orange",
							message: data.message,
						});
						return;
					}
					let msg = data.message || __("Done.");
					if (data.failed_orders && data.failed_orders.length > 0) {
						msg += "<br><br>" + __("Failed orders:") + "<br>";
						msg += data.failed_orders
							.map((f) => `${f.name}: ${f.error}`)
							.join("<br>");
					}
					frappe.msgprint({
						title: __("Bulk Sync Metafields"),
						indicator: data.failed === 0 ? "green" : "orange",
						message: msg,
					});
				},
			});
		},
	});
	d.show();
}
