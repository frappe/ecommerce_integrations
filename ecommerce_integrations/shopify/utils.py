# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _, _dict

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.shopify.constants import (
	MODULE_NAME,
	OLD_SETTINGS_DOCTYPE,
	SETTING_DOCTYPE,
)


def create_shopify_log(**kwargs):
	return create_log(module_def=MODULE_NAME, **kwargs)


def migrate_from_old_connector(payload=None, request_id=None):
	"""This function is called to migrate data from old connector to new connector."""

	if request_id:
		log = frappe.get_doc("Ecommerce Integration Log", request_id)
	else:
		log = create_shopify_log(
			status="Queued",
			method="ecommerce_integrations.shopify.utils.migrate_from_old_connector",
		)

	frappe.enqueue(
		method=_migrate_items_to_ecommerce_item,
		queue="long",
		is_async=True,
		log=log,
	)


def ensure_old_connector_is_disabled():
	try:
		old_setting = frappe.get_doc(OLD_SETTINGS_DOCTYPE)
	except Exception:
		frappe.clear_last_message()
		return

	if old_setting.enable_shopify:
		link = frappe.utils.get_link_to_form(OLD_SETTINGS_DOCTYPE, OLD_SETTINGS_DOCTYPE)
		msg = _("Please disable old Shopify integration from {0} to proceed.").format(link)
		frappe.throw(msg)


def _migrate_items_to_ecommerce_item(log):
	shopify_fields = ["shopify_product_id", "shopify_variant_id"]

	for field in shopify_fields:
		if not frappe.db.exists({"doctype": "Custom Field", "fieldname": field}):
			return

	items = _get_items_to_migrate()

	try:
		_create_ecommerce_items(items)
	except Exception:
		log.status = "Error"
		log.traceback = frappe.get_traceback()
		log.save()
		return

	frappe.db.set_value(SETTING_DOCTYPE, SETTING_DOCTYPE, "is_old_data_migrated", 1)
	log.status = "Success"
	log.save()


def _get_items_to_migrate() -> list[_dict]:
	"""get all list of items that have shopify fields but do not have associated ecommerce item."""

	old_data = frappe.db.sql(
		"""SELECT item.name as erpnext_item_code, shopify_product_id, shopify_variant_id, item.variant_of, item.has_variants
			FROM tabItem item
			LEFT JOIN `tabEcommerce Item` ei on ei.erpnext_item_code = item.name
			WHERE ei.erpnext_item_code IS NULL AND shopify_product_id IS NOT NULL""",
		as_dict=True,
	)

	return old_data or []


def _create_ecommerce_items(items: list[_dict]) -> None:
	for item in items:
		if not all((item.erpnext_item_code, item.shopify_product_id, item.shopify_variant_id)):
			continue

		ecommerce_item = frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": MODULE_NAME,
				"erpnext_item_code": item.erpnext_item_code,
				"integration_item_code": item.shopify_product_id,
				"variant_id": item.shopify_variant_id,
				"variant_of": item.variant_of,
				"has_variants": item.has_variants,
			}
		)
		ecommerce_item.save()
