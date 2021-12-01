import frappe
from frappe import _

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.woocommerce.constants import (
	MODULE_NAME,
	PRODUCT_GROUP,
	SETTINGS_DOCTYPE,
)


def create_ecommerce_items():
	frappe.reload_doc(MODULE_NAME, "doctype", SETTINGS_DOCTYPE)
	filters = {"item_group": _(PRODUCT_GROUP, frappe.get_single("System Settings").language or "en")}
	for item in frappe.db.get_all("Item", filters=filters, fields=["*"]):
		if not frappe.db.exists("Ecommerce Item", {"erpnext_item_code": item.name}):
			_create_ecommerce_item(item)


def _create_ecommerce_item(item):
	ecomm_item = frappe.new_doc("Ecommerce Item")
	ecomm_item.integration = MODULE_NAME
	ecomm_item.erpnext_item_code = item.name
	ecomm_item.integration_item_code = item.sku
	ecomm_item.has_variants = 0
	ecomm_item.sku = item.sku
	ecomm_item.flags.ignore_mandatory = True
	ecomm_item.save(ignore_permissions=True)
