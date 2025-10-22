import json
from collections import defaultdict
from datetime import date, datetime

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import now_datetime

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	ORDER_CODE_FIELD,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	RETURN_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
)


def fully_cancel_orders(unicommerce_order_codes: list[str]) -> None:
	"""Perform "cancel" action on ERPNext sales orders which are fully cancelled in Unicommerce."""

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
	"""Check all recently updated orders for partial cancellations."""

	recently_changed_orders = _filter_recent_orders(orders)

	for order in recently_changed_orders:
		so_data = client.get_sales_order(order["code"])
		if not so_data:
			continue
		update_erpnext_order_items(so_data)


def _filter_recent_orders(orders, time_limit=60 * 12):
	"""Only consider recently updated orders"""
	check_timestamp = (datetime.utcnow().timestamp() - time_limit * 60) * 1000
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
	items = [d.as_dict() for d in erpnext_items if d.get(ORDER_ITEM_CODE_FIELD) not in cancelled_items]

	# add `docname` same as name, required for Update Items functionality
	for item in items:
		item["docname"] = item["name"]
	return items


def _serialize_items(trans_items) -> str:
	# serialie date/datetime objects to string
	for item in trans_items:
		for k, v in item.items():
			if isinstance(v, datetime | date):
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
		["name", ORDER_CODE_FIELD, CHANNEL_ID_FIELD],
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
		credit_note = create_credit_note(invoice.name)
		credit_note.save()


def get_return_warehouse(facility_code):
	return frappe.db.get_value(
		"Unicommerce Warehouses", {"unicommerce_facility_code": facility_code}, "return_warehouse"
	)


def create_credit_note(invoice_name):
	credit_note = make_sales_return(invoice_name)
	facility_code = credit_note.get(FACILITY_CODE_FIELD)
	return_warehouse = get_return_warehouse(facility_code)

	for item in credit_note.items:
		item.warehouse = return_warehouse or item.warehouse

	for tax in credit_note.taxes:
		tax.item_wise_tax_detail = json.loads(tax.item_wise_tax_detail)
		for _item, tax_distribution in tax.item_wise_tax_detail.items():
			tax_distribution[1] *= -1
		tax.item_wise_tax_detail = json.dumps(tax.item_wise_tax_detail)

	return credit_note


def check_and_update_customer_initiated_returns(orders, client: UnicommerceAPIClient) -> None:
	"""Create credit note if order contains customer intiated returns."""

	recently_changed_orders = _filter_recent_orders(orders)

	for order in recently_changed_orders:
		so_data = client.get_sales_order(order["code"])
		if not so_data:
			continue
		sync_customer_initiated_returns(so_data)


def sync_customer_initiated_returns(so_data):
	customer_returns = [r for r in so_data.get("returns", []) if r["type"] == "Customer Returned"]
	if not customer_returns:
		return

	for customer_return in customer_returns:
		if not frappe.db.exists("Sales Invoice", {RETURN_CODE_FIELD: customer_return["code"]}):
			create_cir_credit_note(so_data, customer_return)


def create_cir_credit_note(so_data, return_data):
	sales_order_name = frappe.db.get_value("Sales Order", {ORDER_CODE_FIELD: so_data["code"]})
	so = frappe.get_doc("Sales Order", sales_order_name)

	# Get items from SO which are returned, map SO item -> SI item with linked rows.
	so_item_code_map = {item.get(ORDER_ITEM_CODE_FIELD): item.name for item in so.items}

	invoice_name = frappe.db.get_value("Sales Invoice", {ORDER_CODE_FIELD: so_data["code"], "is_return": 0})
	si = frappe.get_doc("Sales Invoice", invoice_name)
	so_si_item_map = {item.so_detail: item.name for item in si.items}

	credit_note = create_credit_note(si.name)

	credit_note.set(TRACKING_CODE_FIELD, return_data.get("trackingNumber"))
	credit_note.set(SHIPPING_PROVIDER_CODE, return_data.get("shippingProvider"))

	returned_so_codes = [item.get("saleOrderItemCode") for item in return_data.get("returnItems")]
	returned_si_items = [so_si_item_map.get(so_item_code_map.get(code)) for code in returned_so_codes]

	if set(returned_si_items) != set(so_si_item_map.values()):
		_handle_partial_returns(credit_note, returned_si_items)
		pass

	credit_note.save()


def _handle_partial_returns(credit_note, returned_items: list[str]) -> None:
	"""Remove non-returned item from credit note and update taxes"""

	item_code_to_qty_map = defaultdict(float)
	for item in credit_note.items:
		item_code_to_qty_map[item.item_code] += item.qty

	# remove non-returned items
	credit_note.items = [item for item in credit_note.items if item.sales_invoice_item in returned_items]

	returned_qty_map = defaultdict(float)
	for item in credit_note.items:
		returned_qty_map[item.item_code] += item.qty

	for tax in credit_note.taxes:
		# reduce total value
		item_wise_tax_detail = json.loads(tax.item_wise_tax_detail)
		new_tax_amt = 0.0

		for item_code, tax_distribution in item_wise_tax_detail.items():
			# item_code: [rate, amount]
			if not tax_distribution[1]:
				# Ignore 0 values
				continue
			return_percent = returned_qty_map.get(item_code, 0.0) / item_code_to_qty_map.get(item_code)
			tax_distribution[1] *= return_percent
			new_tax_amt += tax_distribution[1]

		tax.tax_amount = new_tax_amt
		tax.item_wise_tax_detail = json.dumps(item_wise_tax_detail)
