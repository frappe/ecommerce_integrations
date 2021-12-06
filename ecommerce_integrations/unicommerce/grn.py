import frappe
from frappe import _

from ecommerce_integrations.unicommerce.constants import GRN_STOCK_ENTRY_TYPE, SETTINGS_DOCTYPE


def validate_stock_entry_for_grn(doc, method=None):
	stock_entry = doc

	if stock_entry.stock_entry_type != GRN_STOCK_ENTRY_TYPE:
		return

	grn_enabled = frappe.db.get_single_value(SETTINGS_DOCTYPE, "use_stock_entry_for_grn")

	if not grn_enabled:
		frappe.throw(
			_("Auto GRN not enabled in Unicommerce settings. Can not use Stock Entry Type: {}").format(
				GRN_STOCK_ENTRY_TYPE
			)
		)

	settings = frappe.get_doc(SETTINGS_DOCTYPE)

	if not settings.is_enabled():
		return

	warehouses = {d.t_warehouse for d in doc.items}
	mapped_warehouses = set(settings.get_erpnext_warehouses(all_wh=True))

	unknown_warehouse = warehouses - mapped_warehouses
	if unknown_warehouse:
		msg = _("Following warehouses do not have Unicommerce facilities mapped to them.")
		msg += "<br>"
		msg += ",".join(unknown_warehouse)
		frappe.throw(msg, title="Unmapped Unicommerce Facility")


def upload_grn(doc, method=None):
	pass
