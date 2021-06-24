from typing import Any, Dict, Iterator, NewType, Optional, Set

import frappe

from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	MODULE_NAME,
	ORDER_CODE_FIELD,
	SETTINGS_DOCTYPE,
)
from ecommerce_integrations.unicommerce.product import import_product_from_unicommerce
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

UnicommerceOrder = NewType("UnicommerceOrder", Dict[str, Any])


def sync_new_orders(client: UnicommerceAPIClient = None, force=False):
	"""This is called from a scheduled job and syncs all new orders from last synced time."""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)

	if not settings.is_enabled():
		return

	# check if need to run based on configured sync frequency.
	# Note: This also updates last_order_sync if function runs.
	if not force and not need_to_run(SETTINGS_DOCTYPE, "order_sync_frequency", "last_order_sync"):
		return

	if client is None:
		client = UnicommerceAPIClient()

	new_orders = _get_new_orders(client, from_date=settings.last_order_sync)
	if new_orders is None:
		return

	for order in new_orders:
		create_order(order, client=client)


def _get_new_orders(
	client: UnicommerceAPIClient, from_date: str
) -> Optional[Iterator[UnicommerceOrder]]:

	"""Search new sales order from unicommerce."""

	uni_orders = client.search_sales_order(from_date=from_date)
	configured_channels = {
		c.channel_id
		for c in frappe.get_all("Unicommerce Channel", filters={"enabled": 1}, fields="channel_id")
	}
	if uni_orders is None:
		return

	for order in uni_orders:
		if order["channel"] not in configured_channels:
			continue
		if frappe.db.exists("Sales Order", {ORDER_CODE_FIELD: order["code"]}):
			continue

		order = client.get_sales_order(order_code=order["code"])
		if order:
			yield order


def create_order(payload: UnicommerceOrder, request_id: Optional[str] = None, client=None) -> None:

	if request_id is None:
		log = create_unicommerce_log(method="self", request_data=payload)
		request_id = log.name
	if client is None:
		client = UnicommerceAPIClient()

	order = payload
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	try:
		_validate_item_list(order)
	except Exception as e:
		create_unicommerce_log(status="Error", exception=e)
	else:
		create_unicommerce_log(status="Success")


def _validate_item_list(order: UnicommerceOrder, client: UnicommerceAPIClient) -> Set[str]:
	"""Ensure all items are synced before processing order.

	If not synced then product sync for specific item is initiated"""

	items = {so_item["itemSku"] for so_item in order["saleOrderItems"]}

	for item in items:
		if ecommerce_item.is_synced(integration=MODULE_NAME, integration_item_code=item):
			continue
		else:
			import_product_from_unicommerce(sku=item, client=client)
	return items
