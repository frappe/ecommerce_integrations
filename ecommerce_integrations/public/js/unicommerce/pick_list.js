frappe.ui.form.on("Pick List", {
	refresh(frm) {
		if (frm.doc.order_details) {
			frm.add_custom_button(__("Generate Invoice"), () =>
				frm.trigger("generate_invoice")
			);
		}
	},
	generate_invoice(frm) {
		let selected_so = [];
		var tbl = frm.doc.order_details || [];
		for (var i = 0; i < tbl.length; i++) {
			selected_so.push(tbl[i].sales_order);
		}
		let sales_orders = [];
		let so_item_list = [];
		const warehouse_allocation = {};
		selected_so.forEach(function (so) {
			const item_details = frm.doc.locations.map((item) => {
				if (item.sales_order == so && item.picked_qty > 0) {
					so_item_list.push({
						so_item: item.sales_order_item,
						qty: item.qty,
					});
					return {
						sales_order_row: item.sales_order_item,
						item_code: item.item_code,
						warehouse: item.warehouse,
						shelf: item.shelf,
					};
				} else {
					return {};
				}
			});
			sales_orders.push(so);
			warehouse_allocation[so] = item_details.filter(
				(value) => Object.keys(value).length !== 0
			);
		});
		frappe.call({
			method: "ecommerce_integrations.unicommerce.invoice.generate_unicommerce_invoices",
			args: {
				sales_orders: sales_orders,
				warehouse_allocation: warehouse_allocation,
			},
			freeze: true,
			freeze_message:
				"Requesting Invoice generation. Once synced, invoice will appear in linked documents.",
		});
	},
});
