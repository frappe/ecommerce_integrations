from collections import Counter
from typing import Dict, List, Tuple

import frappe
from frappe import _dict
from frappe.utils import cint, now
from shopify.resources import InventoryLevel, Variant

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.utils import create_shopify_log


def update_inventory_on_shopify() -> None:
	"""Upload stock levels from ERPNext to Shopify.

	Called by scheduler on configured interval.
	"""
	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		return

	warehous_map = _get_warehouse_map(setting)
	inventory_levels = _get_inventory_levels(warehouses=tuple(warehous_map.keys()))

	if inventory_levels:
		upload_inventory_data_to_shopify(inventory_levels, warehous_map)


@temp_shopify_session
def upload_inventory_data_to_shopify(inventory_levels, warehous_map) -> None:
	synced_on = now()

	for d in inventory_levels:
		d.shopify_location_id = warehous_map[d.warehouse]

		try:
			variant = Variant.find(d.variant_id)
			inventory_id = variant.inventory_item_id

			result = InventoryLevel.set(
				location_id=d.shopify_location_id,
				inventory_item_id=inventory_id,
				available=cint(d.actual_qty),  # shopify doesn't support fractional quantity TODO: docs
			)
			frappe.db.set_value("Ecommerce Item", d.ecom_item, "inventory_synced_on", synced_on)
			d.status = "Success"
		except Exception as e:
			create_shopify_log(method="update_inventory_on_shopify", status="Error", exception=e)
			d.status = "Failed"

	_log_inventory_update_status(inventory_levels)


def _get_warehouse_map(setting) -> Dict[str, str]:
	"""Get mapping from ERPNext warehouse to shopify location id."""

	return {
		wh.erpnext_warehouse: wh.shopify_location_id
		for wh in setting.shopify_warehouse_mapping
	}


def _get_inventory_levels(warehouses: Tuple[str]) -> List[_dict]:
	"""
	Get list of dict containing items that need to be updated on Shopify.

	returns: ecom_item, item_code, variant_id, actual_qty, warehouse

	"""
	data = frappe.db.sql(
		f"""
			SELECT ei.name as ecom_item, bin.item_code as item_code, variant_id, actual_qty, warehouse
			FROM `tabEcommerce Item` ei
				JOIN tabBin bin
				ON ei.erpnext_item_code = bin.item_code
			WHERE bin.warehouse in ({', '.join('%s' for _ in warehouses)})
				AND bin.modified > ei.inventory_synced_on
				AND integration = 'Shopify'
		""",
		warehouses,
		as_dict=1,
	)

	return data


def _log_inventory_update_status(inventory_levels) -> None:
	"""Create log of inventory update."""
	log_message = "variant_id,location_id,status\n"

	log_message += "\n".join(
		f"{d.variant_id},{d.shopify_location_id},{d.status}" for d in inventory_levels
	)

	stats = Counter([d.status for d in inventory_levels])

	percent_successful = stats["Success"] / (stats["Success"] + stats["Failed"])

	if percent_successful == 0:
		status = "Failed"
	elif percent_successful < 1:
		status = "Partial Success"
	else:
		status = "Success"

	log_message = f"Updated {percent_successful * 100}% items\n\n" + log_message

	create_shopify_log(
		method="update_inventory_on_shopify", status=status, message=log_message
	)
