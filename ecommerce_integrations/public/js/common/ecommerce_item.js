frappe.provide("frappe.ui.form");

frappe.ui.form.EcommerceItemQuickEntryForm = class EcommerceItemQuickEntryForm extends (
	frappe.ui.form.QuickEntryForm
) {
	render_dialog() {
		this.mandatory = this.mandatory.concat(this.get_variant_fields());
		super.render_dialog();
	}
	insert() {
		/**
		 * Using alias fieldnames because the doctype definition define them as readonly fields.
		 * Therefor, resulting in the fields being "hidden".
		 */
		const map_field_names = {
			d_integration: "integration",
			d_erpnext_item_code: "erpnext_item_code",
			d_sku: "sku",
		};

		Object.entries(map_field_names).forEach(([fieldname, new_fieldname]) => {
			this.dialog.doc[new_fieldname] = this.dialog.doc[fieldname];
			delete this.dialog.doc[fieldname];
		});

		return super.insert();
	}
	get_variant_fields() {
		var variant_fields = [
			{
				label: __("Integration"),
				fieldname: "d_integration",
				fieldtype: "Link",
				options: "Module Def",
				reqd: 1.
			},
			{
				label: __("ERPNext Item Code"),
				fieldname: "d_erpnext_item_code",
				fieldtype: "Link",
				options: "Item",
				reqd: 1.
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Data",
				label: __("SKU"),
				fieldname: "d_sku",
				reqd: 1.
			},
		];

		return variant_fields;
	}
};
