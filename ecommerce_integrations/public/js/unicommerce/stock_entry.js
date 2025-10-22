frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		if (frm.doc.stock_entry_type == "GRN on Unicommerce") {
			frm.add_custom_button(
				__("Open GRNs"),
				function () {
					frappe.call({
						method: "ecommerce_integrations.unicommerce.utils.get_unicommerce_document_url",
						args: {
							code: "",
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
