from typing import Any, Dict

import frappe
from frappe.utils import cint, now

from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import MODULE_NAME, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log


def update_inventory_on_unicommerce(client=None, force=False):
	"""Update ERPnext warehouse wise inventory to Unicommerce.

	This function gets called by scheduler every minute. The function
	decides whether to run or not based on configured sync frequency.

	force=True ignores the set frequency.
	"""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)

	if not settings.is_enabled() or not settings.enable_inventory_sync:
		return

	# check if need to run based on configured sync frequency
	if not force and not need_to_run(
		SETTINGS_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"
	):
		return

	# get configured warehouses
	warehouses = settings.get_erpnext_warehouses()
	wh_to_facility_map = settings.get_erpnext_to_integration_wh_mapping()

	if client is None:
		client = UnicommerceAPIClient()

	for warehouse in warehouses:
		inventory_synced_on = now()
		erpnext_inventory = get_inventory_levels(warehouses=(warehouse,), integration=MODULE_NAME)
		if not erpnext_inventory:
			continue  # nothing to update

		# TODO: consider reserved qty on both platforms.
		inventory_map = {d.integration_item_code: cint(d.actual_qty) for d in erpnext_inventory}
		facility_code = wh_to_facility_map[warehouse]

		# XXX: batching required? limit on max inventory update in single request?
		response, status = client.bulk_inventory_update(
			facility_code=facility_code, inventory_map=inventory_map
		)

		if status:
			_update_inventory_sync_status(response, erpnext_inventory, inventory_synced_on)


def _update_inventory_sync_status(
	unicommerce_response: Dict[str, bool], ecommerce_item_map: Dict[str, Any], timestamp: str
) -> None:
	successful_skus = [sku for sku, status in unicommerce_response.items() if status]
	sku_to_ecom_item_map = {d.integration_item_code: d.ecom_item for d in ecommerce_item_map}

	for sku in successful_skus:
		ecom_item = sku_to_ecom_item_map[sku]
		update_inventory_sync_status(ecom_item, timestamp)
