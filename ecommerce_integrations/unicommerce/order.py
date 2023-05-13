import json
from collections import defaultdict, namedtuple
from typing import Any, Dict, Iterator, List, NewType, Optional, Set, Tuple

import frappe
from frappe.utils import add_to_date, flt

from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	CHANNEL_TAX_ACCOUNT_FIELD_MAP,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	IS_COD_CHECKBOX,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	ORDER_ITEM_BATCH_NO,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	PACKAGE_TYPE_FIELD,
	SETTINGS_DOCTYPE,
	TAX_FIELDS_MAPPING,
	TAX_RATE_FIELDS_MAPPING,
)
from ecommerce_integrations.unicommerce.customer import sync_customer
from ecommerce_integrations.unicommerce.product import import_product_from_unicommerce
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

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

	status = "COMPLETE" if settings.only_sync_completed_orders else None

	new_orders = _get_new_orders(client, status=status)

	if new_orders is None:
		return

	for order in new_orders:
		sales_order = create_order(order, client=client)

		if settings.only_sync_completed_orders:
			_create_sales_invoices(order, sales_order, client)


def _get_new_orders(
	client: UnicommerceAPIClient, status: Optional[str]
) -> Optional[Iterator[UnicommerceOrder]]:

	"""Search new sales order from unicommerce."""

	updated_since = 24 * 60  # minutes
	uni_orders = client.search_sales_order(updated_since=updated_since, status=status)
	configured_channels = {
		c.channel_id
		for c in frappe.get_all("Unicommerce Channel", filters={"enabled": 1}, fields="channel_id")
	}
	if uni_orders is None:
		return

	for order in uni_orders:
		if order["channel"] not in configured_channels:
			continue

		# In case a sales invoice is not generated for some reason and is skipped, we need to create it manually. Therefore, I have commented out this line of code.
		order = client.get_sales_order(order_code=order["code"])
		if order:
			yield order


def _create_sales_invoices(unicommerce_order, sales_order, client: UnicommerceAPIClient):
	"""Create sales invoice from sales orders, used when integration is only
	syncing finshed orders from Unicommerce."""
	from ecommerce_integrations.unicommerce.invoice import create_sales_invoice

	facility_code = sales_order.get(FACILITY_CODE_FIELD)
	shipping_packages = unicommerce_order["shippingPackages"]
	for package in shipping_packages:
		try:
			# This code was added because the log statement below was being executed every time.
			invoice_data = client.get_sales_invoice(
				shipping_package_code=package["code"], facility_code=facility_code
			)
			existing_si = frappe.db.get_value(
				"Sales Invoice", {INVOICE_CODE_FIELD: invoice_data["invoice"]["code"]}
			)
			if existing_si:
				continue

			log = create_unicommerce_log(method="create_sales_invoice", make_new=True)
			frappe.flags.request_id = log.name

			warehouse_allocations = _get_warehouse_allocations(sales_order)
			create_sales_invoice(
				invoice_data["invoice"],
				sales_order.name,
				update_stock=1,
				so_data=unicommerce_order,
				warehouse_allocations=warehouse_allocations,
			)
		except Exception as e:
			create_unicommerce_log(status="Error", exception=e, rollback=True, request_data=invoice_data)
			frappe.flags.request_id = None
		else:
			create_unicommerce_log(status="Success", request_data=invoice_data)
			frappe.flags.request_id = None


def create_order(payload: UnicommerceOrder, request_id: Optional[str] = None, client=None) -> None:

	order = payload

	existing_so = frappe.db.get_value("Sales Order", {ORDER_CODE_FIELD: order["code"]})
	if existing_so:
		so = frappe.get_doc("Sales Order", existing_so)
		return so

	# If a sales order already exists, then every time it's executed
	if request_id is None:
		log = create_unicommerce_log(
			method="ecommerce_integrations.unicommerce.order.create_order", request_data=payload
		)
		request_id = log.name

	if client is None:
		client = UnicommerceAPIClient()

	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	try:
		_sync_order_items(order, client=client)
		customer = sync_customer(order)
		order = _create_order(order, customer)
	except Exception as e:
		create_unicommerce_log(status="Error", exception=e, rollback=True)
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

	is_cancelled = order["status"] == "CANCELLED"

	facility_code = _get_facility_code(order["saleOrderItems"])
	company_address, dispatch_address = settings.get_company_addresses(facility_code)

	so = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer.name,
			"naming_series": channel_config.sales_order_series or settings.sales_order_series,
			ORDER_CODE_FIELD: order["code"],
			ORDER_STATUS_FIELD: order["status"],
			CHANNEL_ID_FIELD: order["channel"],
			FACILITY_CODE_FIELD: facility_code,
			IS_COD_CHECKBOX: bool(order["cod"]),
			"transaction_date": get_unicommerce_date(order["displayOrderDateTime"]),
			"delivery_date": get_unicommerce_date(order["fulfillmentTat"]),
			"ignore_pricing_rule": 1,
			"items": _get_line_items(
				order["saleOrderItems"], default_warehouse=channel_config.warehouse, is_cancelled=is_cancelled
			),
			"company": channel_config.company,
			"taxes": get_taxes(order["saleOrderItems"], channel_config),
			"tax_category": get_dummy_tax_category(),
			"company_address": company_address,
			"dispatch_address_name": dispatch_address,
			"currency": order.get("currencyCode"),
		}
	)

	so.flags.raw_data = order
	so.save()
	so.submit()

	if is_cancelled:
		so.cancel()

	return so


def _get_line_items(
	line_items, default_warehouse: Optional[str] = None, is_cancelled: bool = False
) -> List[Dict[str, Any]]:

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	wh_map = settings.get_integration_to_erpnext_wh_mapping(all_wh=True)
	so_items = []

	for item in line_items:
		if not is_cancelled and item.get("statusCode") == "CANCELLED":
			continue

		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		warehouse = wh_map.get(item["facilityCode"]) or default_warehouse

		so_items.append(
			{
				"item_code": item_code,
				"rate": item["sellingPrice"],
				"qty": 1,
				"stock_uom": "Nos",
				"warehouse": warehouse,
				ORDER_ITEM_CODE_FIELD: item.get("code"),
				ORDER_ITEM_BATCH_NO: _get_batch_no(item),
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
	item_wise_tax_map = {tax_head: {} for tax_head in TAX_FIELDS_MAPPING.keys()}

	tax_account_map = {
		tax_head: channel_config.get(account_field)
		for tax_head, account_field in CHANNEL_TAX_ACCOUNT_FIELD_MAP.items()
	}
	for item in line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		for tax_head, unicommerce_field in TAX_FIELDS_MAPPING.items():
			tax_amount = flt(item.get(unicommerce_field)) or 0.0
			tax_rate_field = TAX_RATE_FIELDS_MAPPING.get(tax_head, "")
			tax_rate = item.get(tax_rate_field, 0.0)

			tax_map[tax_head] += tax_amount

			item_wise_tax_map[tax_head][item_code] = [tax_rate, tax_amount]

	taxes = []

	for tax_head, value in tax_map.items():
		if not value:
			continue
		taxes.append(
			{
				"charge_type": "Actual",
				"account_head": tax_account_map[tax_head],
				"tax_amount": value,
				"description": tax_head.replace("_", " ").upper(),
				"item_wise_tax_detail": json.dumps(item_wise_tax_map[tax_head]),
				"dont_recompute_tax": 1,
			}
		)

	return taxes


def _get_facility_code(line_items) -> str:
	facility_codes = {item.get("facilityCode") for item in line_items}

	if len(facility_codes) > 1:
		frappe.throw("Multiple facility codes found in single order")

	return list(facility_codes)[0]


def update_shipping_info(doc, method=None):
	"""When package type is changed, update the shipping information on unicommerce."""

	so = doc

	if not so.has_value_changed(PACKAGE_TYPE_FIELD):
		return
	package_type = so.get(PACKAGE_TYPE_FIELD)

	if not package_type:
		return
	frappe.enqueue(_update_package_info_on_unicommerce, queue="short", so_code=so.name)


def _update_package_info_on_unicommerce(so_code):
	try:
		client = UnicommerceAPIClient()

		so = frappe.get_doc("Sales Order", so_code)
		package_type = so.get(PACKAGE_TYPE_FIELD)
		package_info = frappe.get_doc("Unicommerce Package Type", package_type)

		updated_so_data = client.get_sales_order(so.get(ORDER_CODE_FIELD))
		shipping_packages = updated_so_data.get("shippingPackages")

		if not shipping_packages:
			frappe.throw(
				frappe._("Shipping package not present on Unicommerce for order {}").format(so.name)
			)

		shipping_package_code = shipping_packages[0].get("code")

		facility_code = so.get(FACILITY_CODE_FIELD)
		response, status = client.update_shipping_package(
			shipping_package_code=shipping_package_code,
			facility_code=facility_code,
			package_type_code=package_info.package_type_code or "DEFAULT",
			length=package_info.length,
			width=package_info.width,
			height=package_info.height,
		)

		if not status:
			error_message = "Unicommerce integration: Could not update package size\n" + json.dumps(
				response.get("errors"), indent=4
			)
			so.add_comment(text=error_message)
	except Exception as e:
		create_unicommerce_log(status="Error", exception=e)
		raise


def _get_batch_no(so_line_item) -> Optional[str]:
	"""If specified vendor batch code is valid batch number in ERPNext then get batch no.

	SO line items contain batch no detail like this:

	"batchDTO": {
	        "batchCode": "BA000002",
	        "batchFieldsDTO": {
	                "mrp": null,
	                "cost": null,
	                "vendorCode": null,
	                "expiryDate": 1682793000000,
	                "mfd": 1619807400000,
	                "vendorBatchNumber": "1122",
	                "status": "ACTIVE"
	        }
	},
	"""
	batch_no = ((so_line_item.get("batchDTO") or {}).get("batchFieldsDTO") or {}).get(
		"vendorBatchNumber"
	)
	if batch_no and frappe.db.exists("Batch", batch_no):
		return batch_no


def _get_warehouse_allocations(sales_order):
	item_details = []
	for item in sales_order.items:
		item_details.append(
			{
				"sales_order_row": item.name,
				"item_code": item.item_code,
				"warehouse": item.warehouse,
				"batch_no": item.get(ORDER_ITEM_BATCH_NO),
			}
		)
	return item_details
