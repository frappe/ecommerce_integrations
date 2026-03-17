import json
from collections import Counter

import frappe
from frappe.utils import cint, create_batch, now
from pyactiveresource.connection import ResourceNotFound
from shopify import GraphQL

from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.shopify.utils import create_shopify_log


def update_inventory_on_shopify() -> None:
	"""Upload stock levels from ERPNext to Shopify.
	This is

	Called by scheduler on configured interval.
	"""
	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		return

	if not need_to_run(SETTING_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"):
		return

	warehous_map = setting.get_erpnext_to_integration_wh_mapping()
	inventory_levels = get_inventory_levels(tuple(warehous_map.keys()), MODULE_NAME)

	if inventory_levels:
		upload_inventory_data_to_shopify(inventory_levels, warehous_map)


@temp_shopify_session
def upload_inventory_data_to_shopify(inventory_levels, warehous_map) -> None:
	synced_on = now()

	for inventory_sync_batch in create_batch(inventory_levels, 50):
		for d in inventory_sync_batch:
			d.shopify_location_id = warehous_map[d.warehouse]

			try:
				# GraphQL query to get inventory item ID from variant
				variant_query = """
                query($id: ID!) {
                  productVariant(id: $id) {
                    id
                    inventoryItem {
                      id
                      legacyResourceId
                    }
                  }
                }
                """

				variant_gid = f"gid://shopify/ProductVariant/{d.variant_id}"
				variant_response = GraphQL().execute(variant_query, variables={"id": variant_gid})
				variant_result = json.loads(variant_response)

				# Check if variant exists
				variant_data = variant_result.get("data", {}).get("productVariant")
				if not variant_data:
					raise ResourceNotFound("Variant not found")
				inventory_item_gid = variant_data.get("inventoryItem", {}).get("id")

				location_gid = f"gid://shopify/Location/{d.shopify_location_id}"

				activate_mutation = """
					mutation($inventoryItemId: ID!, $locationId: ID!) {
					inventoryActivate(
						inventoryItemId: $inventoryItemId
						locationId: $locationId
					) {
						inventoryLevel {
						id
						}
						userErrors {
						field
						message
						}
					}
					}
					"""

				activate_response = GraphQL().execute(
					activate_mutation,
					variables={
						"inventoryItemId": inventory_item_gid,
						"locationId": location_gid,
					},
				)
				activate_result = json.loads(activate_response)

				# Check activation errors
				activate_errors = (
					activate_result.get("data", {}).get("inventoryActivate", {}).get("userErrors", [])
				)
				if activate_errors:
					pass

				# GraphQL mutation to set inventory level
				inventory_mutation = """
                mutation($inventoryItemId: ID!, $locationId: ID!, $available: Int!) {
                  inventorySetQuantities(
                    input: {
                      reason: "correction"
                      name: "available"
                      ignoreCompareQuantity: true
                      quantities: [
                        {
                          inventoryItemId: $inventoryItemId
                          locationId: $locationId
                          quantity: $available
                        }
                      ]
                    }
                  ) {
                    inventoryAdjustmentGroup {
                      id
                      reason
                    }
                    userErrors {
                      field
                      message
                    }
                  }
                }
                """

				available_qty = cint(d.actual_qty) - cint(d.reserved_qty)

				mutation_response = GraphQL().execute(
					inventory_mutation,
					variables={
						"inventoryItemId": inventory_item_gid,
						"locationId": location_gid,
						"available": available_qty,
					},
				)
				mutation_result = json.loads(mutation_response)

				# Check for errors
				user_errors = (
					mutation_result.get("data", {}).get("inventorySetQuantities", {}).get("userErrors", [])
				)

				if user_errors:
					error_messages = [err.get("message") for err in user_errors]
					raise Exception("; ".join(error_messages))

				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Success"

			except ResourceNotFound:
				# Variant or location is deleted, mark as last synced and ignore.
				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Not Found"
			except Exception as e:
				d.status = "Failed"
				d.failure_reason = str(e)

			# Commit is required here to persist each inventory update independently
			# during bulk Shopify sync to prevent data loss on partial failures
			frappe.db.commit()

		_log_inventory_update_status(inventory_sync_batch)


def _log_inventory_update_status(inventory_levels) -> None:
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

	create_shopify_log(method="update_inventory_on_shopify", status=status, message=log_message)
