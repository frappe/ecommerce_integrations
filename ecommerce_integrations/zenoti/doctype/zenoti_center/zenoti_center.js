// Copyright (c) 2021, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Zenoti Center", {
	refresh(frm) {
		frm.add_custom_button(
			__("Employees"),
			function () {
				frappe.call({
					method: "ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync",
					args: {
						center: frm.doc.name,
						record_type: "Employees",
					},
					callback: function (r) {
						frappe.show_alert({
							message: __("Syncing"),
							indicator: "orange",
						});
					},
				});
			},
			__("Sync")
		);

		frm.add_custom_button(
			__("Customers"),
			function () {
				frappe.call({
					method: "ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync",
					args: {
						center: frm.doc.name,
						record_type: "Customers",
					},
					callback: function (r) {
						frappe.show_alert({
							message: __("Syncing"),
							indicator: "orange",
						});
					},
				});
			},
			__("Sync")
		);

		frm.add_custom_button(
			__("Items"),
			function () {
				frappe.call({
					method: "ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync",
					args: {
						center: frm.doc.name,
						record_type: "Items",
					},
					callback: function (r) {
						frappe.show_alert({
							message: __("Syncing"),
							indicator: "orange",
						});
					},
				});
			},
			__("Sync")
		);

		frm.add_custom_button(
			__("Categories"),
			function () {
				frappe.call({
					method: "ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync",
					args: {
						center: frm.doc.name,
						record_type: "Categories",
					},
					callback: function (r) {
						frappe.show_alert({
							message: __("Syncing"),
							indicator: "orange",
						});
					},
				});
			},
			__("Sync")
		);

		frm.add_custom_button(
			__("Sales Invoice"),
			function () {
				let d = new frappe.ui.Dialog({
					title: __("Sync Sales Invoice"),
					fields: [
						{
							label: "From Date",
							fieldname: "start_date",
							fieldtype: "Date",
							reqd: 1,
						},
						{
							label: "To Date",
							fieldname: "end_date",
							fieldtype: "Date",
							reqd: 1,
						},
					],
					primary_action: function () {
						let data = d.get_values();
						frappe.call({
							method: "ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync",
							args: {
								center: frm.doc.name,
								record_type: "Sales Invoice",
								start_date: data.start_date,
								end_date: data.end_date,
							},
							callback: function (r) {
								frappe.show_alert({
									message: __("Syncing"),
									indicator: "orange",
								});
							},
						});
						d.hide();
					},
					primary_action_label: __("Sync Sales Invoice"),
				});
				d.show();
			},
			__("Sync")
		);

		frm.add_custom_button(
			__("Stock Reconciliation"),
			function () {
				let d = new frappe.ui.Dialog({
					title: __("Sync Stock Reconciliation"),
					fields: [
						{
							label: "Date",
							fieldname: "date",
							fieldtype: "Date",
							reqd: 1,
						},
					],
					primary_action: function () {
						let data = d.get_values();
						frappe.call({
							method: "ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync",
							args: {
								center: frm.doc.name,
								record_type: "Stock Reconciliation",
								start_date: data.date,
							},
							callback: function (r) {
								frappe.show_alert({
									message: __("Syncing"),
									indicator: "orange",
								});
							},
						});
						d.hide();
					},
					primary_action_label: __("Sync Stock Reconciliation"),
				});
				d.show();
			},
			__("Sync")
		);
	},

	setup(frm) {
		frm.set_query("erpnext_cost_center", function () {
			return {
				filters: {
					is_group: 0,
				},
			};
		});

		frm.set_query("erpnext_warehouse", function () {
			return {
				filters: {
					is_group: 0,
				},
			};
		});
	},
});
