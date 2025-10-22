// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on("Unicommerce Shipment Manifest", {
	refresh(frm) {
		if (frm.doc.unicommerce_manifest_code) {
			// add button to open unicommerce order from SO page
			frm.add_custom_button(
				__("Open on Unicommerce"),
				function () {
					frappe.call({
						method: "ecommerce_integrations.unicommerce.utils.get_unicommerce_document_url",
						args: {
							code: frm.doc.unicommerce_manifest_code,
							doctype: frm.doc.doctype,
						},
						callback: function (r) {
							if (!r.exc) {
								window.open(r.message, "_blank");
							}
						},
					});
				},
				__("Unicommerce")
			);
		}
		if (frm.doc.docstatus != 0) {
			return;
		}
		frm.add_custom_button(__("Get Packages"), () => {
			if (
				!(
					frm.doc.channel_id &&
					frm.doc.shipping_method_code &&
					frm.doc.shipping_provider_code
				)
			) {
				frappe.msgprint(
					__(
						"Please select Channel, Shipping method and Shipping provider first"
					)
				);
				return;
			}
			erpnext.utils.map_current_doc({
				method: "ecommerce_integrations.unicommerce.doctype.unicommerce_shipment_manifest.unicommerce_shipment_manifest.get_shipping_package_list",
				source_doctype: "Sales Invoice",
				target: frm.doc,
				setters: [
					{
						fieldtype: "Data",
						label: __("Shipping Package"),
						fieldname: "unicommerce_shipping_package_code",
						default: "",
					},
					{
						fieldtype: "Data",
						label: __("Unicommerce Order"),
						fieldname: "unicommerce_order_code",
						default: "",
					},

					{
						fieldtype: "Data",
						label: __("Tracking Code"),
						fieldname: "unicommerce_tracking_code",
						default: "",
					},
					{
						fieldtype: "Data",
						label: __("Unicommerce Invoice"),
						fieldname: "unicommerce_invoice_code",
						default: "",
					},
				],
				get_query_filters: {
					docstatus: 1,
					unicommerce_shipping_method: frm.doc.shipping_method_code,
					unicommerce_shipping_provider:
						frm.doc.shipping_provider_code,
					unicommerce_channel_id: frm.doc.channel_id,
					unicommerce_manifest_generated: 0,
				},
			});
		});
	},

	scan_barcode: function (frm) {
		if (!frm.doc.scan_barcode) {
			return false;
		}

		frappe
			.xcall(
				"ecommerce_integrations.unicommerce.doctype.unicommerce_shipment_manifest.unicommerce_shipment_manifest.search_packages",
				{
					search_term: frm.doc.scan_barcode,
					shipper: frm.doc.shipping_provider_code,
					channel: frm.doc.channel_id,
				}
			)
			.then((invoice) => {
				if (!invoice) {
					frappe.show_alert({
						message: __("Could not find the package."),
						indicator: "red",
					});
					return;
				}

				let cur_grid = frm.fields_dict.manifest_items.grid;

				const already_exists = frm.doc.manifest_items.find(
					(d) => d.sales_invoice === invoice
				);
				if (already_exists) {
					frappe.show_alert({
						message: __("Package already added in this manifest"),
						indicator: "red",
					});
					return;
				}

				let new_row = frappe.model.add_child(
					frm.doc,
					cur_grid.doctype,
					"manifest_items"
				);

				frappe.model.set_value(
					new_row.doctype,
					new_row.name,
					"sales_invoice",
					invoice
				);
			})
			.finally(() => {
				frm.fields_dict.scan_barcode.set_value("");
				refresh_field("manifest_items");
			});
	},
});
