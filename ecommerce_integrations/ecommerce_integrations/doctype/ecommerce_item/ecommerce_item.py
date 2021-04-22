# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from typing import Optional


class EcommerceItem(Document):
	erpnext_item_code: str      # item_code inside ERPNext
	integration: str            # name of integration
	integration_item_code: str  # unique id of product on integration
	has_variants: int           # is the product a template, i.e. does it have varients
	variant_id: str             # unique id of varient on integration
	variant_of: str             # template id of ERPNext item
	sku: str                    # SKU

	def validate(self):
		self.check_unique_constraints()


	def check_unique_constraints(self):
		filters  = list()

		unique_integration_item_code = {
				"integration": self.integration,
				"erpnext_item_code": self.erpnext_item_code,
				"integration_item_code": self.integration_item_code
				}
		if self.variant_id:
			unique_integration_item_code.update({"variant_id": self.variant_id})
		filters.append(unique_integration_item_code)

		if self.sku:
			unique_sku = { "integration": self.integration, "sku": self.sku }
			filters.append(unique_sku)

		for filter in filters:
			if frappe.db.exists("Ecommerce Item", filter):
				frappe.throw(_("Ecommerce Item already exists"), exc=frappe.DuplicateEntryError)


def is_synced(integration: str,
		integration_item_code: str,
		variant_id: Optional[str] = None,
		sku: Optional[str] = None) -> bool:
	""" Check if item is synced from integration.

		variant_id is optional. Use variant_id to check if particular variant is synced.
		sku is optional. Use SKU alone with integration to check if it's synced.
		E.g.
			integration: shopify,
			integration_item_code: TSHIRT
			variant_id: red_t_shirt
	"""

	if sku:
		return _is_sku_synced(integration, sku)

	filter = {
			"integration" : integration,
			"integration_item_code" : integration_item_code
		}
	if variant_id:
		filter.update({ "variant_id": variant_id })

	return bool(frappe.db.exists("Ecommerce Item", filter))


def _is_sku_synced(integration: str, sku: str) -> bool:
	filter = {"integration" : integration, "sku": sku}
	return bool(frappe.db.exists("Ecommerce Item", filter))


def get_erpnext_item(integration: str,
		integration_item_code: str,
		variant_id: Optional[str] = None,
		sku: Optional[str] = None):

	if sku:
		item_code = frappe.db.get_value("Ecommerce Item", {"sku": sku}, fieldname="erpnext_item_code")
		print(item_code)
	else:
		filter = {
				"integration" : integration,
				"integration_item_code" : integration_item_code
			}
		if variant_id:
			filter.update({ "variant_id": variant_id })
		item_code = frappe.db.get_value("Ecommerce Item", filter, fieldname="erpnext_item_code")

	if item_code:
		return frappe.get_doc("Item", item_code)

	return None
