from typing import List, Tuple

import frappe
from frappe import _dict
from frappe.utils import now
from frappe.utils.nestedset import get_descendants_of


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
				AND ei.integration = %s
		""",
		values=warehouses + (integration,),
		as_dict=1,
	)

	return data


def get_inventory_levels_of_group_warehouse(warehouse: str, integration: str):
	"""Get updated inventory for a single group warehouse.

	If warehouse mapping is done to a group warehouse then consolidation of all
	leaf warehouses is required"""

	child_warehouse = get_descendants_of("Warehouse", warehouse)
	all_warehouses = tuple(child_warehouse) + (warehouse,)

	data = frappe.db.sql(
		f"""
			SELECT ei.name as ecom_item, bin.item_code as item_code,
				integration_item_code,
				variant_id,
				sum(actual_qty) as actual_qty,
				sum(reserved_qty) as reserved_qty,
				max(bin.modified) as last_updated,
				max(ei.inventory_synced_on) as last_synced
			FROM `tabEcommerce Item` ei
				JOIN tabBin bin
				ON ei.erpnext_item_code = bin.item_code
			WHERE bin.warehouse in ({', '.join(['%s'] * len(all_warehouses))})
				AND integration = %s
			GROUP BY
				ei.erpnext_item_code
			HAVING
				last_updated > last_synced
			""",
		values=all_warehouses + (integration,),
		as_dict=1,
	)

	# add warehouse as group warehouse for sending to integrations
	for item in data:
		item.warehouse = warehouse

	return data


def update_inventory_sync_status(ecommerce_item, time=None):
	"""Update `inventory_synced_on` timestamp to specified time or current time (if not specified).

	After updating inventory levels to any integration, the Ecommerce Item should know about when it was last updated.
	"""
	if time is None:
		time = now()

	frappe.db.set_value("Ecommerce Item", ecommerce_item, "inventory_synced_on", time)
