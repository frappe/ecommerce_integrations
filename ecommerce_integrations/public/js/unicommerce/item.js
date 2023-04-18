frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.doc.sync_with_unicommerce) {
			frm.add_custom_button(
				__("Open Unicommerce Item"),
				function () {
					frappe.call({
						method:
							"ecommerce_integrations.unicommerce.utils.get_unicommerce_document_url",
						args: {
							code: frm.doc.item_code,
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
			frm.add_custom_button(
				__("Resync Item"),
				function () {
					frappe.call({
						method:
							"ecommerce_integrations.unicommerce.order.resync_item",
						args: {
							code: frm.doc.item_code
						},
						callback: function (r) {
							frappe.msgprint("Item will resync in few Minutes!")
						},
					});
				},
				__("Unicommerce")
			);
		}
	},
});
