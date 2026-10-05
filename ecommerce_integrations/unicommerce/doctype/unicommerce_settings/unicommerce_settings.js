// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on("Unicommerce Settings", {
	refresh(frm) {
		if (!frm.doc.enable_unicommerce) {
			return;
		}

		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {
				integration: "Unicommerce",
			});
		});

		let sync_buttons = ["Items", "Orders", "Inventory"];

		sync_buttons.forEach((action) => {
			frm.add_custom_button(
				action,
				() => {
					frappe.call({
						method: "ecommerce_integrations.unicommerce.utils.force_sync",
						args: {
							document: action,
						},
						callback: (r) => {
							if (!r.exc) {
								frappe.msgprint(__(`Intiated ${action} Sync.`));
							}
						},
					});
				},
				__("Sync Now"),
			);
		});

		// Backfill: sync old orders.
		frm.add_custom_button(
			__("Sync Old Orders"),
			() => {
				frappe.prompt(
					[
						{
							fieldname: "from_date",
							label: __("From Date"),
							fieldtype: "Date",
							reqd: 1,
						},
						{
							fieldname: "to_date",
							label: __("To Date (inclusive)"),
							fieldtype: "Date",
							reqd: 1,
						},
					],
					(values) => {
						if (values.to_date < values.from_date) {
							frappe.msgprint(
								__("To Date cannot be before From Date."),
							);
							return;
						}
						// Match server MAX_SELECTABLE_RANGE_DAYS (31 inclusive calendar days).
						const max_days = 31;
						const from = frappe.datetime.str_to_obj(
							values.from_date,
						);
						const to = frappe.datetime.str_to_obj(values.to_date);
						const inclusive_days =
							frappe.datetime.get_diff(to, from) + 1;
						if (inclusive_days > max_days) {
							frappe.msgprint(
								__(
									"Date range cannot be longer than {0} days.",
									[max_days],
								),
							);
							return;
						}
						frappe.call({
							method: "ecommerce_integrations.unicommerce.sync_old_orders.enqueue_sync_old_orders",
							args: {
								from_date: values.from_date,
								to_date: values.to_date,
							},
							freeze: true,
							freeze_message: __("Queuing old-order sync..."),
							callback: (r) => {
								if (!r.exc) {
									frappe.msgprint(
										__(
											"Old-order sync queued for {0} to {1}. Track progress in the Ecommerce Integration Log.",
											[values.from_date, values.to_date],
										),
									);
								}
							},
						});
					},
					__("Sync Old Orders"),
				);
			},
			__("Sync Now"),
		);
	},

	onload: function (frm) {
		// naming series options
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});

		frm.fields_dict["warehouse_mapping"].grid.get_field(
			"erpnext_warehouse",
		).get_query = function (doc) {
			return {
				filters: {
					disabled: 0,
				},
			};
		};
	},
});
