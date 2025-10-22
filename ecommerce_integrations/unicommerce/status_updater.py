import frappe

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.cancellation_and_returns import (
	check_and_update_customer_initiated_returns,
	create_rto_return,
	fully_cancel_orders,
	update_partially_cancelled_orders,
)
from ecommerce_integrations.unicommerce.constants import (
	ORDER_CODE_FIELD,
	ORDER_STATUS_FIELD,
	SETTINGS_DOCTYPE,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PACKAGE_STATUS_FIELD,
)

ORDER_STATES = ["PENDING_VERIFICATION", "CREATED", "PROCESSING", "COMPLETE", "CANCELLED"]
PARTIAL_CANCELLED_STATES = ["PENDING_VERIFICATION", "CREATED", "PROCESSING"]
RETURN_POSSIBLE_STATE = ["COMPLETE"]

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

SHIPMENT_RETURN_STATES = ["RETURN_EXPECTED", "RETURNED"]

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

	enabled_channels = frappe.db.get_list("Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id")
	valid_orders = [order for order in updated_orders if order.get("channel") in enabled_channels]
	if valid_orders:
		_update_order_status_fields(valid_orders)

	fully_cancelled_orders = [d["code"] for d in valid_orders if d["status"] == "CANCELLED"]
	if fully_cancelled_orders:
		fully_cancel_orders(fully_cancelled_orders)

	probable_partial_cancels = [d for d in valid_orders if d["status"] in PARTIAL_CANCELLED_STATES]
	if probable_partial_cancels:
		update_partially_cancelled_orders(probable_partial_cancels, client=client)

	probable_returns = [d for d in valid_orders if d["status"] in RETURN_POSSIBLE_STATE]
	if probable_returns:
		check_and_update_customer_initiated_returns(probable_returns, client=client)


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


def ignore_pick_list_on_sales_order_cancel(doc, method=None):
	"""Ignore pick list doctype links while cancelling Sales Order"""

	ignored_links = list(doc.ignore_linked_doctypes or [])
	ignored_links.append("Pick List")
	doc.ignore_linked_doctypes = ignored_links


def update_shipping_package_status():
	"""Periodically update changed shipping package info in ERPNext."""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.is_enabled():
		return

	client = UnicommerceAPIClient()

	days_to_sync = min(settings.get("order_status_days") or 2, 14)
	minutes = days_to_sync * 24 * 60

	# find all Facilities
	enabled_facilities = list(settings.get_integration_to_erpnext_wh_mapping().keys())
	enabled_channels = frappe.db.get_list("Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id")

	for facility in enabled_facilities:
		updated_packages = client.search_shipping_packages(updated_since=minutes, facility_code=facility)
		valid_packages = [p for p in updated_packages if p.get("channel") in enabled_channels]

		if not valid_packages:
			continue
		_update_package_status_fields(valid_packages)

		returning_packages = [p for p in valid_packages if p["status"] in SHIPMENT_RETURN_STATES]
		if returning_packages:
			for package in returning_packages:
				create_rto_return(package, client=client)


def _update_package_status_fields(packages):
	package_status_map = {d["code"]: d["status"] for d in packages}
	package_codes = list(package_status_map.keys())

	current_package_status = frappe.db.get_values(
		"Sales Invoice",
		{SHIPPING_PACKAGE_CODE_FIELD: ("in", package_codes)},
		fieldname=["name", SHIPPING_PACKAGE_STATUS_FIELD, SHIPPING_PACKAGE_CODE_FIELD],
		as_dict=True,
	)

	for invoice in current_package_status:
		uni_code = invoice.get(SHIPPING_PACKAGE_CODE_FIELD)
		old_status = invoice.get(SHIPPING_PACKAGE_STATUS_FIELD)
		new_status = package_status_map.get(uni_code)

		if old_status != new_status:
			si_code = invoice["name"]
			frappe.db.set_value(
				"Sales Invoice", si_code, SHIPPING_PACKAGE_STATUS_FIELD, new_status, for_update=True
			)
