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

	if updated_orders:
		_update_order_status_fields(updated_orders)


def _update_order_status_fields(orders):

	enabled_channels = frappe.db.get_list(
		"Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id"
	)

	valid_orders = [order for order in orders if order.get("channel") in enabled_channels]

	order_status_map = {d["code"]: d["status"] for d in valid_orders}
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
