from collections import defaultdict, namedtuple
from typing import Any, Dict, Iterator, List, NewType, Optional, Set

import frappe
from frappe.utils import add_to_date, flt

from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	CHANNEL_TAX_ACCOUNT_FIELD_MAP,
	FACILITY_CODE_FIELD,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	ORDER_STATUS_FIELD,
	SETTINGS_DOCTYPE,
	TAX_FIELDS_MAPPING,
)
from ecommerce_integrations.unicommerce.customer import sync_customer
from ecommerce_integrations.unicommerce.product import import_product_from_unicommerce
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

UnicommerceOrder = NewType("UnicommerceOrder", Dict[str, Any])

SoItem = namedtuple("SoItem", ["item_code", "rate", "warehouse"])


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
		_sync_order_items(order, client=client)
		customer = sync_customer(order)
		order = _create_order(order, customer)
	except Exception as e:
		create_unicommerce_log(status="Error", exception=e)
		frappe.flags.request_id = None
	else:
		create_unicommerce_log(status="Success")
		frappe.flags.request_id = None
		return order


def _sync_order_items(order: UnicommerceOrder, client: UnicommerceAPIClient) -> Set[str]:
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
			"items": _get_line_items(order["saleOrderItems"], default_warehouse=channel_config.warehouse),
			"company": channel_config.company,
			"taxes": get_taxes(order["saleOrderItems"], channel_config),
			"tax_category": get_dummy_tax_category(),
		}
	)

	so.save()
	so.submit()

	return so


def _get_line_items(line_items, default_warehouse: Optional[str] = None) -> List[Dict[str, Any]]:

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	wh_map = settings.get_integration_to_erpnext_wh_mapping(all_wh=True)

	consolidated_item_qty = _get_consolidate_qty(line_items, wh_map)

	so_items = []

	for item, qty in consolidated_item_qty.items():
		so_items.append(
			{
				"item_code": item.item_code,
				"rate": item.rate,
				"qty": qty,
				"stock_uom": "Nos",
				"warehouse": item.warehouse or default_warehouse,
			}
		)
	return so_items


def get_taxes(line_items, channel_config) -> List:
	taxes = []

	# Note: Tax details are NOT available during SO stage.
	# Fields are also different hence during SO stage this function won't capture GST.
	# Same function is also used in invoice to recompute accurate tax and charges.
	# When invoice is created, tax details are added.
	tax_map = {tax_head: 0.0 for tax_head in TAX_FIELDS_MAPPING.keys()}

	tax_account_map = {
		tax_head: channel_config.get(account_field)
		for tax_head, account_field in CHANNEL_TAX_ACCOUNT_FIELD_MAP.items()
	}
	for item in line_items:
		for tax_head, unicommerce_field in TAX_FIELDS_MAPPING.items():
			tax_map[tax_head] += flt(item.get(unicommerce_field)) or 0.0

	taxes = []

	for tax_head, value in tax_map.items():
		if not value:
			continue
		taxes.append(
			{
				"charge_type": "Actual",
				"account_head": tax_account_map[tax_head],
				"tax_amount": value,
				"description": tax_head.replace("_", " "),
			}
		)

	return taxes


def _get_facility_code(line_items) -> str:
	facility_codes = {item.get("facilityCode") for item in line_items}

	if len(facility_codes) > 1:
		frappe.throw("Multiple facility codes found in single order")

	return list(facility_codes)[0]


def _get_consolidate_qty(line_items, wh_map) -> Dict[SoItem, int]:
	consolidated_item_qty = defaultdict(int)
	for item in line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		so_item = SoItem(
			item_code=item_code, rate=item["sellingPrice"], warehouse=wh_map.get(item["facilityCode"]),
		)
		consolidated_item_qty[so_item] += 1

	return consolidated_item_qty
