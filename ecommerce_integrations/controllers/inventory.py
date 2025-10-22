import frappe
from frappe import _dict
from frappe.query_builder import DocType
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
	EcommerceItem = DocType("Ecommerce Item")
	Bin = DocType("Bin")

	query = (
		frappe.qb.from_(EcommerceItem)
		.join(Bin)
		.on(EcommerceItem.erpnext_item_code == Bin.item_code)
		.select(
			EcommerceItem.name.as_("ecom_item"),
			Bin.item_code.as_("item_code"),
			EcommerceItem.integration_item_code,
			EcommerceItem.variant_id,
			Bin.actual_qty,
			Bin.warehouse,
			Bin.reserved_qty,
		)
		.where(
			(Bin.warehouse.isin(warehouses))
			& (Bin.modified > EcommerceItem.inventory_synced_on)
			& (EcommerceItem.integration == integration)
		)
	)

	return query.run(as_dict=1)


def get_inventory_levels_of_group_warehouse(warehouse: str, integration: str):
	"""Get updated inventory for a single group warehouse.

	If warehouse mapping is done to a group warehouse then consolidation of all
	leaf warehouses is required"""

	child_warehouse = get_descendants_of("Warehouse", warehouse)
	all_warehouses = (*tuple(child_warehouse), warehouse)

	EcommerceItem = DocType("Ecommerce Item")
	Bin = DocType("Bin")

	query = (
		frappe.qb.from_(EcommerceItem)
		.join(Bin)
		.on(EcommerceItem.erpnext_item_code == Bin.item_code)
		.select(
			EcommerceItem.name.as_("ecom_item"),
			Bin.item_code.as_("item_code"),
			EcommerceItem.integration_item_code,
			EcommerceItem.variant_id,
			Sum(Bin.actual_qty).as_("actual_qty"),
			Sum(Bin.reserved_qty).as_("reserved_qty"),
			Max(Bin.modified).as_("last_updated"),
			Max(EcommerceItem.inventory_synced_on).as_("last_synced"),
		)
		.where((Bin.warehouse.isin(all_warehouses)) & (EcommerceItem.integration == integration))
		.groupby(EcommerceItem.erpnext_item_code)
		.having(Max(Bin.modified) > Max(EcommerceItem.inventory_synced_on))
	)

	data = query.run(as_dict=1)

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
