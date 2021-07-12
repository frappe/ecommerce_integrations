from typing import Any, Dict, Iterator, List, NewType, Optional, Set

import frappe
from frappe.utils import add_to_date, flt

from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	SETTINGS_DOCTYPE,
)
from ecommerce_integrations.unicommerce.customer import sync_customer
from ecommerce_integrations.unicommerce.product import import_product_from_unicommerce
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date

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

	new_orders = _get_new_orders(client, from_date=add_to_date(settings.last_order_sync, days=-1))
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

	order = payload

	if request_id is None:
		log = create_unicommerce_log(
			method="ecommerce_integrations.unicommerce.order.create_order", request_data=payload
		)
		request_id = log.name

	existing_so = frappe.db.get_value("Sales Order", {ORDER_CODE_FIELD: order["code"]})
	if existing_so:
		so = frappe.get_doc("Sales Order", existing_so)
		create_unicommerce_log(status="Invalid", message="Sales Order already exists, skipped")
		return so

	if client is None:
		client = UnicommerceAPIClient()

	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	try:
		_validate_item_list(order, client=client)
		customer = sync_customer(order)
		order = _create_order(order, customer)
	except Exception as e:
		create_unicommerce_log(status="Error", exception=e)
		frappe.flags.request_id = None
	else:
		create_unicommerce_log(status="Success")
		frappe.flags.request_id = None
		return order


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


def _create_order(order: UnicommerceOrder, customer) -> None:

	channel_config = frappe.get_doc("Unicommerce Channel", order["channel"])
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)

	so = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer.name,
			"naming_series": channel_config.sales_order_series or settings.sales_order_series,
			ORDER_CODE_FIELD: order["code"],
			ORDER_STATUS_FIELD: order["status"],
			CHANNEL_ID_FIELD: order["channel"],
			FACILITY_CODE_FIELD: _get_facility_code(order["saleOrderItems"]),
			"transaction_date": get_unicommerce_date(order["displayOrderDateTime"]),
			"delivery_date": get_unicommerce_date(order["fulfillmentTat"]),
			"ignore_pricing_rule": 1,
			"items": _get_line_items(order, default_warehouse=channel_config.warehouse),
			"company": channel_config.company,
			"taxes": _get_taxes(order, channel_config),
		}
	)

	so.save()

	return so


def _get_line_items(
	order: UnicommerceOrder, default_warehouse: Optional[str] = None
) -> List[Dict[str, Any]]:

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	wh_map = settings.get_integration_to_erpnext_wh_mapping()
	line_items = order["saleOrderItems"]

	so_items = []
	for item in line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		so_items.append(
			{
				"item_code": item_code,
				"rate": item["sellingPrice"],
				"qty": 1,
				"stock_uom": "Nos",
				"warehouse": wh_map.get(item["facilityCode"], default_warehouse),
				ORDER_ITEM_CODE_FIELD: item.get("code"),
			}
		)
	return so_items


def _get_taxes(order: UnicommerceOrder, channel_config) -> List:
	line_items = order["saleOrderItems"]
	taxes = []

	# Note: Tax details are not available during SO stage.
	# When invoice is created, tax details are added and consider "included in rate"

	taxes.extend(_get_shipping_line(line_items, channel_config))
	return taxes


def _get_shipping_line(line_items, channel_config):

	total_shipping_cost = 0.0

	for item in line_items:
		total_shipping_cost += flt(item.get("shippingCharges"))
		total_shipping_cost += flt(item.get("shippingMethodCharges"))

	return [
		{
			"charge_type": "Actual",
			"account_head": channel_config.fnf_account,
			"tax_amount": total_shipping_cost,
			"description": "Shipping charges",
		}
	]


def _get_facility_code(line_items) -> str:
	facility_codes = {item.get("facilityCode") for item in line_items}

	if len(facility_codes) > 1:
		frappe.throw("Multiple facility codes found in single order")

	return list(facility_codes)[0]
