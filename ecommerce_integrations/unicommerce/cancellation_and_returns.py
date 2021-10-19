import json
from datetime import date, datetime
from typing import List

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import now_datetime

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	ORDER_CODE_FIELD,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)


def fully_cancel_orders(unicommerce_order_codes: List[str]) -> None:
	""" Perform "cancel" action on ERPNext sales orders which are fully cancelled in Unicommerce."""

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


def update_partially_cancelled_orders(orders, client: UnicommerceAPIClient) -> None:
	""" Check all recently updated orders for partial cancellations."""

	recently_changed_orders = _filter_recent_orders(orders)

	for order in recently_changed_orders:
		so_data = client.get_sales_order(order["code"])
		if not so_data:
			continue
		update_erpnext_order_items(so_data)


def _filter_recent_orders(orders, time_limit=60 * 6):
	""" Only consider recently updated orders """
	check_timestamp = (now_datetime().timestamp() - time_limit * 60) * 1000
	return [order for order in orders if int(order["updated"]) >= check_timestamp]


def update_erpnext_order_items(so_data, so=None):
	"""Update cancelled items in ERPNext order."""
	cancelled_items = [d["code"] for d in so_data["saleOrderItems"] if d["statusCode"] == "CANCELLED"]
	if not cancelled_items:
		return

	if not so:
		so_name = frappe.db.get_value("Sales Order", {ORDER_CODE_FIELD: so_data["code"]})
		if not so_name:
			return
		so = frappe.get_doc("Sales Order", so_name)

	if so.docstatus > 1:
		return

	new_items = _delete_cancelled_items(so.items, cancelled_items)

	if len(so.items) == len(new_items):
		return

	update_child_qty_rate(
		parent_doctype="Sales Order",
		trans_items=_serialize_items(new_items),
		parent_doctype_name=so.name,
	)


def _delete_cancelled_items(erpnext_items, cancelled_items):
	items = [
		d.as_dict() for d in erpnext_items if d.get(ORDER_ITEM_CODE_FIELD) not in cancelled_items
	]

	# add `docname` same as name, required for Update Items functionality
	for item in items:
		item["docname"] = item["name"]
	return items


def _serialize_items(trans_items) -> str:
	# serialie date/datetime objects to string
	for item in trans_items:
		for k, v in item.items():
			if isinstance(v, (datetime, date)):
				item[k] = str(v)

	return json.dumps(trans_items)


def create_rto_return(package_info, client: UnicommerceAPIClient):
	"""When RTO is expected create a credit note in draft state with required details.

	RTO => Return To Origin. Entire package is being returned.
	"""

	package_code = package_info["code"]

	invoice = frappe.db.get_value(
		"Sales Invoice",
		{SHIPPING_PACKAGE_CODE_FIELD: package_code},
		["name", ORDER_CODE_FIELD],
		as_dict=True,
	)

	already_returned = frappe.db.get_value(
		"Sales Invoice", {SHIPPING_PACKAGE_CODE_FIELD: package_code, "is_return": 1}
	)
	if not invoice or already_returned:
		return

	so_data = client.get_sales_order(invoice.get(ORDER_CODE_FIELD))

	rto_returns = [
		r for r in so_data["returns"] if r["type"] == "Courier Returned" and r["code"] == package_code
	]
	if rto_returns:
		credit_note = make_sales_return(invoice.name)
		credit_note.save()
