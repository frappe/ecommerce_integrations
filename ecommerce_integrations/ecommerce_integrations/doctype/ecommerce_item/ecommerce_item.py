# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import frappe
from erpnext import get_default_company
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, get_datetime, now


class EcommerceItem(Document):
	erpnext_item_code: str  # item_code in ERPNext
	integration: str  # name of integration
	integration_item_code: str  # unique id of product on integration
	variant_id: str  # unique id of product variant on integration
	has_variants: int  # is the product a template, i.e. does it have varients
	variant_of: str  # template id of ERPNext item
	sku: str  # SKU

	def validate(self):
		self.set_defaults()

	def before_insert(self):
		self.check_unique_constraints()

	def check_unique_constraints(self) -> None:
		filters = []

		unique_integration_item_code = {
			"integration": self.integration,
			"erpnext_item_code": self.erpnext_item_code,
			"integration_item_code": self.integration_item_code,
		}

		if self.variant_id:
			unique_integration_item_code.update({"variant_id": self.variant_id})

		filters.append(unique_integration_item_code)

		if self.sku:
			unique_sku = {"integration": self.integration, "sku": self.sku}
			filters.append(unique_sku)

		for filter in filters:
			if frappe.db.exists("Ecommerce Item", filter):
				frappe.throw(_("Ecommerce Item already exists"), exc=frappe.DuplicateEntryError)

	def set_defaults(self):
		if not self.inventory_synced_on:
			# set to start of epoch time i.e. not synced
			self.inventory_synced_on = get_datetime("1970-01-01")


def is_synced(
	integration: str,
	integration_item_code: str,
	variant_id: str | None = None,
	sku: str | None = None,
) -> bool:
	"""Check if item is synced from integration.

	sku is optional. Use SKU alone with integration to check if it's synced.
	E.g.
	        integration: shopify,
	        integration_item_code: TSHIRT
	"""
	filter = {"integration": integration, "integration_item_code": integration_item_code}

	if variant_id:
		filter.update({"variant_id": variant_id})

	item_exists = bool(frappe.db.exists("Ecommerce Item", filter))

	if not item_exists and sku:
		return _is_sku_synced(integration, sku)
	return item_exists


def _is_sku_synced(integration: str, sku: str) -> bool:
	filter = {"integration": integration, "sku": sku}
	return bool(frappe.db.exists("Ecommerce Item", filter))


def get_erpnext_item_code(
	integration: str,
	integration_item_code: str,
	variant_id: str | None = None,
	has_variants: int | None = 0,
) -> str | None:
	filters = {"integration": integration, "integration_item_code": integration_item_code}
	if variant_id:
		filters.update({"variant_id": variant_id})
	elif has_variants:
		filters.update({"has_variants": 1})

	return frappe.db.get_value("Ecommerce Item", filters, fieldname="erpnext_item_code")


def get_erpnext_item(
	integration: str,
	integration_item_code: str,
	variant_id: str | None = None,
	sku: str | None = None,
	has_variants: int | None = 0,
):
	"""Get ERPNext item for specified ecommerce_item.

	Note: If variant_id is not specified then item is assumed to be single OR template.
	"""

	item_code = None
	if sku:
		item_code = frappe.db.get_value(
			"Ecommerce Item", {"sku": sku, "integration": integration}, fieldname="erpnext_item_code"
		)
	if not item_code:
		item_code = get_erpnext_item_code(
			integration, integration_item_code, variant_id=variant_id, has_variants=has_variants
		)

	if item_code:
		return frappe.get_doc("Item", item_code)


def create_ecommerce_item(
	integration: str,
	integration_item_code: str,
	item_dict: dict,
	variant_id: str | None = None,
	sku: str | None = None,
	variant_of: str | None = None,
	has_variants=0,
) -> None:
	"""Create Item in erpnext and link it with Ecommerce item doctype.

	item_dict contains fields necessary to populate Item doctype.
	"""

	# SKU not allowed for template items
	sku = cstr(sku) if not has_variants else None

	if is_synced(integration, integration_item_code, variant_id, sku):
		return

	# crete default item
	item = {
		"doctype": "Item",
		"is_stock_item": 1,
		"is_sales_item": 1,
		"item_defaults": [{"company": get_default_company()}],
	}

	item.update(item_dict)

	new_item = frappe.get_doc(item)
	new_item.flags.from_integration = True
	new_item.insert(ignore_permissions=True, ignore_mandatory=True)

	ecommerce_item = frappe.get_doc(
		{
			"doctype": "Ecommerce Item",
			"integration": integration,
			"erpnext_item_code": new_item.name,
			"integration_item_code": integration_item_code,
			"has_variants": has_variants,
			"variant_id": cstr(variant_id),
			"variant_of": cstr(variant_of),
			"sku": sku,
			"item_synced_on": now(),
		}
	)

	ecommerce_item.insert()
