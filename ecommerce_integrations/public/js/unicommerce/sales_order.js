frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.unicommerce_order_code) {
			// add button to open unicommerce order from SO page
			frm.add_custom_button(
				__("Open Unicommerce Order"),
				function () {
					frappe.call({
						method: "ecommerce_integrations.unicommerce.utils.get_unicommerce_document_url",
						args: {
							code: frm.doc.unicommerce_order_code,
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
		if (
			frm.doc.unicommerce_order_code &&
			frm.doc.docstatus == 1 &&
			flt(frm.doc.per_billed, 6) < 100
		) {
			// remove default button
			frm.remove_custom_button("Sales Invoice", "Create");
			const so_code = frm.doc.name;

			const item_details = frm.doc.items.map((item) => {
				// each row is assumed to be for 1 qty.
				return {
					sales_order_row: item.name,
					item_code: item.item_code,
					warehouse: item.warehouse,
				};
			});

			const warehouse_allocation = {};
			warehouse_allocation[so_code] = item_details;

			frm.add_custom_button(
				__("Generate Invoice"),
				function () {
					frappe.call({
						method: "ecommerce_integrations.unicommerce.invoice.generate_unicommerce_invoices",
						args: {
							sales_orders: [so_code],
							warehouse_allocation: warehouse_allocation,
						},
						freeze: true,
						freeze_message:
							"Requesting Invoice generation. Once synced, invoice will appear in linked documents.",
						callback: function (r) {
							frm.reload_doc();
						},
					});
				},
				__("Unicommerce")
			);
		}
	},
});
