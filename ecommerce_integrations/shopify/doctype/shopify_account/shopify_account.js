// Copyright (c) 2024, Frappe and contributors
// For license information, please see LICENSE

frappe.provide("ecommerce_integrations.shopify.shopify_account");

frappe.ui.form.on("Shopify Account", {
	onload: function (frm) {
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});

		// Set up form description
		frm.set_intro(__("This record serves as the Shopify Settings for a single Shopify store. Create one record per store."));
	},

	fetch_shopify_locations: function (frm) {
		frappe.call({
			doc: frm.doc,
			method: "fetch_shopify_locations",
			callback: (r) => {
				if (!r.exc) refresh_field("warehouse_mappings");
			},
		});
	},

	refresh: function (frm) {
		// Make shop_domain read-only after save
		if (!frm.doc.__islocal) {
			frm.set_df_property("shop_domain", "read_only", 1);
		}

		frm.add_custom_button(__("Import Products"), function () {
			if (frm.doc.enabled && frm.doc.shop_domain) {
				frappe.set_route("shopify-import-products", {"account": frm.doc.name});
			} else {
				frappe.msgprint(__("Please enable the account and save before importing products"));
			}
		});
		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {
				integration: "Shopify",
				reference_document: frm.doc.name
			});
		});
		frm.add_custom_button(__("Fetch Shopify Locations"), function () {
			if (!frm.doc.enabled) {
				frappe.msgprint(__("Please enable the account first"));
				return;
			}
			frm.trigger("fetch_shopify_locations");
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
		frm.set_query("erpnext_warehouse", "warehouse_mappings", warehouse_query);

		frm.set_query("selling_price_list", () => {
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

		frm.set_query("default_customer", () => {
			const filters = {disabled: 0};
			if (frm.doc.company) {
				filters.company = frm.doc.company;
			}
			return {filters};
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

		frm.set_query("tax_account", "tax_mappings", tax_query);
	},

	// Additional event handlers specific to Shopify Account
	enabled: function (frm) {
		frm.trigger("toggle_conditional_fields");
		frm.trigger("show_enabled_status");
	},

	company: function (frm) {
		if (frm.doc.company) {
			if (frm.doc.warehouse_mappings && frm.doc.warehouse_mappings.length > 0) {
				frappe.msgprint({
					title: __("Company Changed"),
					message: __("Please review warehouse and tax mappings to ensure they belong to the selected company."),
					indicator: "orange"
				});
			}
		}
		frm.trigger("setup_queries");
	},

	shop_domain: function (frm) {
		if (frm.doc.shop_domain) {
			let domain = frm.doc.shop_domain.replace(/^https?:\/\//, "");
			if (domain && !domain.endsWith(".myshopify.com")) {
				frappe.msgprint({
					title: __("Invalid Domain"),
					message: __("Shop domain must end with '.myshopify.com'"),
					indicator: "red"
				});
			}
			frm.set_value("shop_domain", domain);
		}
	},

	sync_sales_invoice: function (frm) {
		frm.trigger("validate_sync_dependencies");
	},

	sync_delivery_note: function (frm) {
		frm.trigger("validate_sync_dependencies");
	},

	create_customers: function (frm) {
		if (!frm.doc.create_customers && !frm.doc.default_customer) {
			frappe.msgprint({
				title: __("Default Customer Required"),
				message: __("When automatic customer creation is disabled, a default customer should be set."),
				indicator: "orange"
			});
		}
	},

	toggle_conditional_fields: function (frm) {
		const is_enabled = frm.doc.enabled;
		frm.toggle_reqd("access_token", is_enabled);
		frm.toggle_reqd("shared_secret", is_enabled);
		frm.toggle_reqd("company", is_enabled);
	},

	show_enabled_status: function (frm) {
		if (frm.doc.enabled) {
			if (!frm.doc.access_token || !frm.doc.shared_secret || !frm.doc.company) {
				frm.dashboard.add_indicator(__("Incomplete Setup"), "orange");
			} else {
				frm.dashboard.add_indicator(__("Enabled"), "green");
			}
		} else {
			frm.dashboard.add_indicator(__("Disabled"), "red");
		}
	},

	validate_sync_dependencies: function (frm) {
		if ((frm.doc.sync_sales_invoice || frm.doc.sync_delivery_note) && !frm.doc.cost_center) {
			frappe.msgprint({
				title: __("Cost Center Recommended"),
				message: __("A cost center is recommended when Sales Invoice or Delivery Note sync is enabled."),
				indicator: "orange"
			});
		}
	},
});

// Handle warehouse mapping child table events
frappe.ui.form.on("Shopify Warehouse Mapping", {
	erpnext_warehouse: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.erpnext_warehouse && frm.doc.company) {
			frappe.db.get_value("Warehouse", row.erpnext_warehouse, "company")
				.then(r => {
					if (r.message && r.message.company !== frm.doc.company) {
						frappe.msgprint(__("Selected warehouse does not belong to company {0}", [frm.doc.company]));
						frappe.model.set_value(cdt, cdn, "erpnext_warehouse", "");
					}
				});
		}
	}
});

// Handle tax mapping child table events
frappe.ui.form.on("Shopify Tax Account", {
	tax_account: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.tax_account && frm.doc.company) {
			frappe.db.get_value("Account", row.tax_account, "company")
				.then(r => {
					if (r.message && r.message.company !== frm.doc.company) {
						frappe.msgprint(__("Selected account does not belong to company {0}", [frm.doc.company]));
						frappe.model.set_value(cdt, cdn, "tax_account", "");
					}
				});
		}
	}
});
