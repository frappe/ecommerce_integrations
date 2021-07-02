// Copyright (c) 2021, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Unicommerce Settings", {
	refresh(frm) {
		if (!frm.doc.enable_unicommerce) {
			return;
		}

		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", "List");
		});

		let sync_buttons = ["Items", "Orders", "Inventory"];

		sync_buttons.forEach((action) => {
			frm.add_custom_button(
				action,
				() => {
					frappe.call({
						method:
							"ecommerce_integrations.unicommerce.utils.force_sync",
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
				__("Sync Now")
			);
		});
	},
});
