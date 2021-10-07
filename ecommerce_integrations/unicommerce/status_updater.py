from typing import List

import frappe

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	ORDER_CODE_FIELD,
	ORDER_STATUS_FIELD,
	SETTINGS_DOCTYPE,
)

ORDER_STATES = ["PENDING_VERIFICATION", "CREATED", "PROCESSING", "COMPLETE", "CANCELLED"]
SHIPMENT_STATES = [
	"CREATED",
	"LOCATION_NOT_SERVICEABLE",
	"PICKING",
	"PICKED",
	"PACKED",
	"READY_TO_SHIP",
	"CANCELLED",
	"MANIFESTED",
	"DISPATCHED",
	"SHIPPED",
	"DELIVERED",
	"PENDING_CUSTOMIZATION",
	"CUSTOMIZATION_COMPLETE",
	"RETURN_EXPECTED",
	"RETURNED",
	"SPLITTED",
	"RETURN_ACKNOWLEDGED",
	"MERGED",
]

ORDER_FINAL_STATES = ["COMPLETE", "CANCELLED"]
SHIPMENT_FINAL_STATES = ["DELIVERED", "RETURNED"]


def update_sales_order_status():

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.is_enabled():
		return

	client = UnicommerceAPIClient()

	days_to_sync = min(settings.get("order_status_days") or 2, 14)
	minutes = days_to_sync * 24 * 60
	updated_orders = client.search_sales_order(updated_since=minutes)

	enabled_channels = frappe.db.get_list(
		"Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id"
	)
	valid_orders = [order for order in updated_orders if order.get("channel") in enabled_channels]

	if valid_orders:
		_update_order_status_fields(valid_orders)

	fully_cancelled_orders = [d["code"] for d in valid_orders if d["status"] == "CANCELLED"]

	if fully_cancelled_orders:
		fully_cancel_orders(fully_cancelled_orders)


def _update_order_status_fields(orders):

	order_status_map = {d["code"]: d["status"] for d in orders}
	order_codes = list(order_status_map.keys())

	current_orders_status = frappe.db.get_values(
		"Sales Order",
		{ORDER_CODE_FIELD: ("in", order_codes)},
		fieldname=["name", ORDER_STATUS_FIELD, ORDER_CODE_FIELD],
		as_dict=True,
	)

	for order in current_orders_status:
		uni_code = order.get(ORDER_CODE_FIELD)
		old_status = order.get(ORDER_STATUS_FIELD)
		new_status = order_status_map.get(uni_code)

		if old_status != new_status:
			so_code = order["name"]
			frappe.db.set_value("Sales Order", so_code, ORDER_STATUS_FIELD, new_status, for_update=True)


def fully_cancel_orders(unicommerce_order_codes: List[str]) -> None:

	current_orders_status = frappe.db.get_values(
		"Sales Order",
		{ORDER_CODE_FIELD: ("in", unicommerce_order_codes)},
		fieldname=["name", ORDER_STATUS_FIELD, ORDER_CODE_FIELD, "docstatus"],
		as_dict=True,
	)

	for order in current_orders_status:
		if order.docstatus != 1:
			continue

		linked_sales_invoice = frappe.db.get_value(
			"Sales Invoice", filters={ORDER_CODE_FIELD: order.get(ORDER_CODE_FIELD), "docstatus": 1}
		)
		if not linked_sales_invoice:
			so = frappe.get_doc("Sales Order", order.name)
			so.cancel()


def ignore_pick_list_on_sales_order_cancel(doc, method=None):
	"""Ignore pick list doctype links while cancelling Sales Order"""

	ignored_links = list(doc.ignore_linked_doctypes or [])
	ignored_links.append("Pick List")
	doc.ignore_linked_doctypes = ignored_links
