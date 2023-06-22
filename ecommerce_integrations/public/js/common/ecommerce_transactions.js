frappe.ui.form.on(cur_frm.doctype, {
	refresh(frm) {
		if (frm.doc.amended_from) {
			// see if any taxes present
			if (frm.doc.taxes.find((t) => t.dont_recompute_tax)) {
				frappe.msgprint(
					__(
						"Amending document created via E-Commerce integrations will not re-compute taxes. Please check taxes before submitting."
					),
					__("Warning About Taxes")
				);
			}
		}
	},
});
