from collections import Counter

import frappe
from frappe.utils import cint, create_batch, now
from pyactiveresource.connection import ResourceNotFound
from shopify.resources import InventoryLevel, Variant

from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE, ACCOUNT_DOCTYPE
from ecommerce_integrations.shopify.utils import create_shopify_log


def update_inventory_on_shopify() -> None:
	"""Upload stock levels from ERPNext to Shopify for all enabled accounts.

	Called by scheduler on configured interval.
	"""
	# Get all enabled Shopify accounts
	enabled_accounts = frappe.get_all(ACCOUNT_DOCTYPE, 
		filters={"enabled": 1}, 
		fields=["name", "shop_domain"])
	
	if not enabled_accounts:
		# Fallback to legacy singleton for backward compatibility
		_update_inventory_legacy()
		return
	
	for account_data in enabled_accounts:
		from ecommerce_integrations.shopify.utils import resolve_account_context
		account = resolve_account_context(account_data.name)
		_update_inventory_for_account(account)


def _update_inventory_legacy():
	"""Legacy inventory update using singleton (for backward compatibility)."""
	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		return

	if not need_to_run(SETTING_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"):
		return

	warehous_map = setting.get_erpnext_to_integration_wh_mapping()
	inventory_levels = get_inventory_levels(tuple(warehous_map.keys()), MODULE_NAME)

	if inventory_levels:
		upload_inventory_data_to_shopify(inventory_levels, warehous_map, account=None)


def _update_inventory_for_account(account):
	"""Update inventory for a specific Shopify account."""
	if not account.is_enabled():
		return
	
	# Check if account has inventory sync enabled (assuming this field exists or will be added)
	# For now, we'll assume all enabled accounts want inventory sync
	# TODO: Add inventory sync toggle to Shopify Account doctype if needed
	
	# Use account-specific scheduling check
	if not need_to_run(ACCOUNT_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync", account.name):
		return
	
	# Get warehouse mappings from account
	warehous_map = {}
	for mapping in account.warehouse_mappings or []:
		if mapping.erpnext_warehouse and mapping.shopify_location_id:
			warehous_map[mapping.erpnext_warehouse] = mapping.shopify_location_id
	
	if not warehous_map:
		frappe.log_error(
			f"No warehouse mappings configured for Shopify Account: {account.name}",
			"Shopify Inventory Sync"
		)
		return
	
	inventory_levels = get_inventory_levels(tuple(warehous_map.keys()), MODULE_NAME)
	
	if inventory_levels:
		upload_inventory_data_to_shopify(inventory_levels, warehous_map, account=account)


@temp_shopify_session
def upload_inventory_data_to_shopify(inventory_levels, warehous_map, account=None) -> None:
	synced_on = now()

	for inventory_sync_batch in create_batch(inventory_levels, 50):
		for d in inventory_sync_batch:
			d.shopify_location_id = warehous_map[d.warehouse]

			try:
				variant = Variant.find(d.variant_id)
				inventory_id = variant.inventory_item_id

				InventoryLevel.set(
					location_id=d.shopify_location_id,
					inventory_item_id=inventory_id,
					# shopify doesn't support fractional quantity
					available=cint(d.actual_qty) - cint(d.reserved_qty),
				)
				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Success"
			except ResourceNotFound:
				# Variant or location is deleted, mark as last synced and ignore.
				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Not Found"
			except Exception as e:
				d.status = "Failed"
				d.failure_reason = str(e)

			frappe.db.commit()

		_log_inventory_update_status(inventory_sync_batch, account)


def _log_inventory_update_status(inventory_levels, account=None) -> None:
	"""Create log of inventory update."""
	log_message = "variant_id,location_id,status,failure_reason\n"

	log_message += "\n".join(
		f"{d.variant_id},{d.shopify_location_id},{d.status},{d.failure_reason or ''}"
		for d in inventory_levels
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

	# Include account reference in log if available
	reference_document = account.name if account else None
	create_shopify_log(
		method="update_inventory_on_shopify", 
		status=status, 
		message=log_message,
		reference_document=reference_document
	)
