# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

from collections import Counter

import frappe
from frappe.utils import cint, create_batch, now

from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	get_inventory_levels_of_group_warehouse,
	update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.medusa.connection import MedusaClient
from ecommerce_integrations.medusa.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.medusa.utils import create_medusa_log


def update_inventory_on_medusa() -> None:
	"""Upload ERPNext stock levels to Medusa.

	Called by the scheduler on the configured interval. Mirrors
	``shopify.inventory.update_inventory_on_shopify``.
	"""
	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_medusa:
		return

	# Self-throttle to the configured ``inventory_sync_frequency``.
	if not need_to_run(SETTING_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"):
		return

	warehouse_map = setting.get_erpnext_to_integration_wh_mapping()

	# Build the delta set of inventory levels per mapped warehouse. A warehouse may
	# be a group warehouse, in which case the leaf bins must be consolidated.
	inventory_levels = []
	for erpnext_warehouse in warehouse_map:
		if frappe.db.get_value("Warehouse", erpnext_warehouse, "is_group"):
			levels = get_inventory_levels_of_group_warehouse(erpnext_warehouse, MODULE_NAME)
		else:
			levels = get_inventory_levels((erpnext_warehouse,), MODULE_NAME)
		inventory_levels.extend(levels)

	if inventory_levels:
		upload_inventory_data_to_medusa(inventory_levels, warehouse_map)


def upload_inventory_data_to_medusa(inventory_levels, warehouse_map) -> None:
	"""Push each delta inventory level to Medusa, batched with a per-batch log."""
	client = MedusaClient()
	synced_on = now()

	# Cache variant/sku -> Medusa inventory_item_id lookups across the whole run.
	inventory_item_cache: dict[str, str | None] = {}

	for inventory_sync_batch in create_batch(inventory_levels, 50):
		for d in inventory_sync_batch:
			d.medusa_location_id = warehouse_map[d.warehouse]
			d.failure_reason = None

			try:
				inventory_item_id = _resolve_inventory_item_id(client, d, inventory_item_cache)

				if not inventory_item_id:
					# No Medusa inventory item for this SKU; may be a transient empty
					# lookup, so do NOT advance the watermark - let it retry next cycle.
					d.status = "Not Found"
					d.failure_reason = "No Medusa inventory item found for SKU"
					frappe.db.commit()
					continue

				# Medusa does not support fractional stock quantities.
				stocked_quantity = cint(d.actual_qty) - cint(d.reserved_qty)
				client.update_inventory_level(inventory_item_id, d.medusa_location_id, stocked_quantity)

				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Success"
			except Exception as e:
				# A 404 on the location-level endpoint means the variant or location
				# no longer exists on Medusa — treat like the deleted-variant case.
				if _is_not_found(e):
					update_inventory_sync_status(d.ecom_item, time=synced_on)
					d.status = "Not Found"
				else:
					d.status = "Failed"
					d.failure_reason = str(e)

			frappe.db.commit()

		_log_inventory_update_status(inventory_sync_batch)


def _resolve_inventory_item_id(client, d, cache) -> str | None:
	"""Resolve a delta row's variant to a Medusa ``inventory_item_id``.

	A Medusa variant carries a SKU; each SKU maps to exactly one inventory item.
	We look the inventory item up by SKU. The variant SKU is stored on the
	``Ecommerce Item`` (``sku``); fall back to the item_code when unset.

	# Verified against @medusajs/types 2.4.0; endpoint is
	#   ``GET /admin/inventory-items?sku=<sku>`` returning
	#   ``{inventory_items: [{id, sku, location_levels: [...]}]}``.
	"""
	sku = frappe.db.get_value("Ecommerce Item", d.ecom_item, "sku") or d.item_code
	if not sku:
		return None

	if sku in cache:
		return cache[sku]

	response = client.get("/inventory-items", {"sku": sku})
	inventory_items = response.get("inventory_items", [])
	inventory_item_id = inventory_items[0].get("id") if inventory_items else None

	cache[sku] = inventory_item_id
	return inventory_item_id


def _is_not_found(exception) -> bool:
	"""Best-effort detection of a Medusa 404 from a raised request exception."""
	response = getattr(exception, "response", None)
	return bool(response is not None and getattr(response, "status_code", None) == 404)


def _log_inventory_update_status(inventory_levels) -> None:
	"""Create a per-batch ``Ecommerce Integration Log`` of the inventory update.

	Mirrors ``shopify.inventory._log_inventory_update_status``.
	"""
	log_message = "variant_id,location_id,status,failure_reason\n"

	log_message += "\n".join(
		f"{d.variant_id},{d.medusa_location_id},{d.status},{d.failure_reason or ''}" for d in inventory_levels
	)

	stats = Counter([d.status for d in inventory_levels])

	percent_successful = stats["Success"] / len(inventory_levels)

	if percent_successful == 0:
		status = "Failed"
	elif percent_successful < 1:
		status = "Partial Success"
	else:
		status = "Success"

	log_message = f"Updated {percent_successful * 100}% items\n\n" + log_message

	create_medusa_log(method="update_inventory_on_medusa", status=status, message=log_message)
