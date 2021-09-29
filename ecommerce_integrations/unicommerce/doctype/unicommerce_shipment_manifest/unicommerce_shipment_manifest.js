// Copyright (c) 2021, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Unicommerce Shipment Manifest", {
	scan_barcode: function (frm) {
		let scan_barcode_field = frm.fields_dict.scan_barcode;

		if (!frm.doc.scan_barcode) {
			return false;
		}

		frappe
			.call({
				method:
					"ecommerce_integrations.unicommerce.doctype.unicommerce_shipment_manifest.unicommerce_shipment_manifest.search_packages",
				args: {
					search_term: frm.doc.scan_barcode,
					shipper: frm.doc.shipping_provider_code,
					channel: frm.doc.channel_id,
				},
				freeze: true,
				freeze_message: __("Fetching package with specified AWB code"),
			})
			.then((r) => {
				const invoice = r && r.message;

				if (!invoice) {
					frappe.show_alert({
						message: __("Could not find the package."),
						indicator: "red",
					});
					return;
				}

				let cur_grid = frm.fields_dict.manifest_items.grid;
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

				scan_barcode_field.set_value("");
				refresh_field("manifest_items");
			});
	},
});
