// Copyright (c) 2024, Frappe and contributors
// For license information, please see LICENSE

frappe.provide("ecommerce_integrations.shopify.shopify_account");

frappe.ui.form.on("Shopify Account", {
	onload: function (frm) {
		// Load naming series for document series fields
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				if (r.message) {
					$.each(r.message, (key, value) => {
						set_field_options(key, value);
					});
				}
			},
		});

		// Set up form description
		frm.set_intro(__("This record serves as the Shopify Settings for a single Shopify store. Create one record per store."));
	},

	refresh: function (frm) {
		// Add custom buttons
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
			
			frappe.call({
				doc: frm.doc,
				method: "fetch_shopify_locations",
				callback: (r) => {
					if (!r.exc) {
						frm.refresh_field("warehouse_mappings");
						frappe.msgprint(__("Shopify locations fetched successfully"));
					}
				},
			});
		});

		frm.trigger("setup_queries");
		frm.trigger("toggle_conditional_fields");
		frm.trigger("show_enabled_status");
	},

	enabled: function (frm) {
		frm.trigger("toggle_conditional_fields");
		frm.trigger("show_enabled_status");
	},

	company: function (frm) {
		if (frm.doc.company) {
			// Warn user to review mappings when company changes
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
		// Auto-format shop domain
		if (frm.doc.shop_domain) {
			let domain = frm.doc.shop_domain.replace(/^https?:\/\//, "");
			if (domain && !domain.endsWith(".myshopify.com")) {
				// Don't auto-append, let validation handle it
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
		// Show/hide fields based on enabled status
		const is_enabled = frm.doc.enabled;
		
		// Make credentials mandatory when enabled
		frm.toggle_reqd("access_token", is_enabled);
		frm.toggle_reqd("shared_secret", is_enabled);
		frm.toggle_reqd("company", is_enabled);
	},

	show_enabled_status: function (frm) {
		// Show status indicator
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

	setup_queries: function (frm) {
		// Warehouse queries - filter by company
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

		// Price list query - only selling price lists
		frm.set_query("selling_price_list", () => {
			return {
				filters: {
					selling: 1,
				},
			};
		});

		// Cost center query - filter by company
		frm.set_query("cost_center", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: "No",
				},
			};
		});

		// Customer query - filter by company if set
		frm.set_query("default_customer", () => {
			const filters = {disabled: 0};
			if (frm.doc.company) {
				filters.company = frm.doc.company;
			}
			return {filters};
		});

		// Tax account queries
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
});

// Handle warehouse mapping child table events
frappe.ui.form.on("Shopify Warehouse Mapping", {
	erpnext_warehouse: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.erpnext_warehouse && frm.doc.company) {
			// Validate warehouse belongs to the same company
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
			// Validate tax account belongs to the same company
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
