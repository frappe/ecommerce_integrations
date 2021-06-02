from typing import List, Tuple

import frappe
from frappe import _dict
from frappe.utils import now


def get_inventory_levels(warehouses: Tuple[str], integration: str) -> List[_dict]:
	"""
	Get list of dict containing items for which the inventory needs to be updated on Integeration.

	New inventory levels are identified by checking Bin modification timestamp,
	so ensure that if you sync the inventory with integration, you have also
	updated `inventory_synced_on` field in related Ecommerce Item.

	returns: list of _dict containing ecom_item, item_code, integration_item_code, variant_id, actual_qty, warehouse, reserved_qty
	"""
	data = frappe.db.sql(
		f"""
			SELECT ei.name as ecom_item, bin.item_code as item_code, integration_item_code, variant_id, actual_qty, warehouse, reserved_qty
			FROM `tabEcommerce Item` ei
				JOIN tabBin bin
				ON ei.erpnext_item_code = bin.item_code
			WHERE bin.warehouse in ({', '.join('%s' for _ in warehouses)})
				AND bin.modified > ei.inventory_synced_on
				AND integration = %s
		""",
		values=warehouses + (integration,),
		as_dict=1,
	)

	return data


def update_inventory_sync_status(ecommerce_item, time=None):
	"""Update `inventory_synced_on` timestamp to specified time or current time (if not specified).

	After updating inventory levels to any integration, the Ecommerce Item should know about when it was last updated.
	"""
	if time is None:
		time = now()

	frappe.db.set_value("Ecommerce Item", ecommerce_item, "inventory_synced_on", time)
