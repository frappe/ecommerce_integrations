import frappe
from frappe import _dict
from frappe.query_builder.functions import Max, Sum
from frappe.utils import now
from frappe.utils.nestedset import get_descendants_of


def get_inventory_levels(warehouses: tuple[str], integration: str) -> list[_dict]:
	"""
	Get list of dict containing items for which the inventory needs to be updated on Integeration.

	New inventory levels are identified by checking Bin modification timestamp,
	so ensure that if you sync the inventory with integration, you have also
	updated `inventory_synced_on` field in related Ecommerce Item.

	returns: list of _dict containing ecom_item, item_code, integration_item_code, variant_id, actual_qty, warehouse, reserved_qty
	"""
	bin = frappe.qb.DocType("Bin")
	ecommerce_item = frappe.qb.DocType("Ecommerce Item")

	return (
		frappe.qb.from_(ecommerce_item)
		.join(bin)
		.on(ecommerce_item.erpnext_item_code == bin.item_code)
		.select(
			ecommerce_item.name.as_("ecom_item"),
			bin.item_code,
			ecommerce_item.integration_item_code,
			ecommerce_item.variant_id,
			bin.actual_qty,
			bin.warehouse,
			bin.reserved_qty,
		)
		.where(bin.warehouse.isin(warehouses))
		.where(bin.modified > ecommerce_item.inventory_synced_on)
		.where(ecommerce_item.integration == integration)
	).run(as_dict=True)


def get_inventory_levels_of_group_warehouse(warehouse: str, integration: str):
	"""Get updated inventory for a single group warehouse.

	If warehouse mapping is done to a group warehouse then consolidation of all
	leaf warehouses is required"""

	child_warehouse = get_descendants_of("Warehouse", warehouse)
	all_warehouses = (*tuple(child_warehouse), warehouse)

	bin = frappe.qb.DocType("Bin")
	ecommerce_item = frappe.qb.DocType("Ecommerce Item")

	data = (
		frappe.qb.from_(ecommerce_item)
		.join(bin)
		.on(ecommerce_item.erpnext_item_code == bin.item_code)
		.select(
			ecommerce_item.name.as_("ecom_item"),
			bin.item_code,
			ecommerce_item.integration_item_code,
			ecommerce_item.variant_id,
			Sum(bin.actual_qty).as_("actual_qty"),
			Sum(bin.reserved_qty).as_("reserved_qty"),
			Max(bin.modified).as_("last_updated"),
			Max(ecommerce_item.inventory_synced_on).as_("last_synced"),
		)
		.where(bin.warehouse.isin(all_warehouses))
		.where(ecommerce_item.integration == integration)
		.groupby(ecommerce_item.erpnext_item_code)
		.having(Max(bin.modified) > Max(ecommerce_item.inventory_synced_on))
	).run(as_dict=True)

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
