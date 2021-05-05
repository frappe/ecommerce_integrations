import json
import frappe

from frappe.utils import cint, now

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from shopify.resources import Variant, InventoryLevel


from typing import List


@temp_shopify_session
def update_inventory_on_shopify():
	"""Upload stock levels from ERPNext to Shopify.

	Called by scheduler on configured interval.

	"""
	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.update_erpnext_stock_levels_to_shopify:
		return

	warehous_map = _get_warehouse_map(setting)
	inventory_levels = _get_inventory_levels(warehouses=warehous_map.keys())
	print(inventory_levels)

	for d in inventory_levels:
		shopify_location = warehous_map[d.warehouse]

		try:
			variant = Variant.find(d.variant_id)
			inventory_id = variant.inventory_item_id

			inventory_level = InventoryLevel.set(
				location_id=shopify_location,
				inventory_item_id=inventory_id,
				available=cint(d.actual_qty),
			)
			frappe.db.set_value("Ecommerce Item", d.ecom_item, "inventory_synced_on", now())
		except Exception as e:
			create_shopify_log(method="update_inventory_on_shopify", status="Error", exception=e)
			continue


def _get_warehouse_map(setting):
	"""Get mapping from ERPNext warehouse to shopify location id."""

	return {
		wh.erpnext_warehouse: wh.shopify_location_id
		for wh in setting.shopify_warehouse_mapping
	}


def _get_inventory_levels(warehouses: List[str]):
	"""
	Get list of dict containing items that need to be updated on Shopify.

	returns: ecom_item, item_code, variant_id, actual_qty, warehouse

	"""
	# for filtering in SQL query
	wh_placeholders = ",".join(["%s" for _ in range(len(warehouses))])

	data = frappe.db.sql(
		"""
			SELECT ei.name as ecom_item, bin.item_code as item_code, variant_id, actual_qty, warehouse
			FROM `tabEcommerce Item` ei
				JOIN tabBin bin
				ON ei.erpnext_item_code = bin.item_code
			WHERE bin.warehouse in ({})
				AND bin.modified > ei.inventory_synced_on
				AND integration = 'Shopify'
			""".format(
			wh_placeholders
		),
		tuple(warehouses),
		as_dict=1,
	)

	return data
