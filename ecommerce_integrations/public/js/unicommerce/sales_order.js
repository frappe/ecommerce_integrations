frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.unicommerce_order_code) {
			// add button to open unicommerce order from SO page
			frm.add_custom_button(
				__("Open Unicommerce Order"),
				function () {
					frappe.call({
						method:
							"ecommerce_integrations.unicommerce.utils.get_unicommerce_document_url",
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
	},
});
