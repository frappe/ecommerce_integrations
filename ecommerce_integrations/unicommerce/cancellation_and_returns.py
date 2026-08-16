import json
from collections import defaultdict
from datetime import date, datetime

import frappe
from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return
from erpnext.accounts.services.child_item_update import update_child_qty_rate
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
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date


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
	"""Create a credit note when RTO is expected (Return To Origin)."""

	package_code = package_info["code"]

	# is_return=0, else this can pick the credit note instead of the invoice
	invoice = frappe.db.get_value(
		"Sales Invoice",
		{SHIPPING_PACKAGE_CODE_FIELD: package_code, "is_return": 0},
		["name", ORDER_CODE_FIELD, CHANNEL_ID_FIELD, FACILITY_CODE_FIELD],
		as_dict=True,
	)

	already_returned = frappe.db.get_value(
		"Sales Invoice", {SHIPPING_PACKAGE_CODE_FIELD: package_code, "is_return": 1}
	)
	if not invoice or already_returned:
		return

	so_data = client.get_sales_order(invoice.get(ORDER_CODE_FIELD))
	if not so_data:
		return

	# the sweep calls us once per package that changed state
	sync_rto_returns(
		so_data, only_package=package_code, client=client, facility_code=invoice.get(FACILITY_CODE_FIELD)
	)


def sync_rto_returns(so_data, only_package=None, client=None, facility_code=None):
	"""Create credit notes for RTO packages in the order payload.

	Payload driven, so it also covers old orders outside the hourly sweep's window.
	An RTO return `code` is the shipping package code, which links to the invoice.
	"""
	rto_returns = [
		r
		for r in so_data.get("returns", [])
		if r["type"] == "Courier Returned" and (not only_package or r["code"] == only_package)
	]

	for rto_return in rto_returns:
		package_code = rto_return["code"]

		# no docstatus filter: credit notes are left in draft, so submitted never matches
		if frappe.db.exists("Sales Invoice", {SHIPPING_PACKAGE_CODE_FIELD: package_code, "is_return": 1}):
			continue

		# returns can only be made against a submitted invoice, never a credit note
		invoice = frappe.db.get_value(
			"Sales Invoice",
			{SHIPPING_PACKAGE_CODE_FIELD: package_code, "is_return": 0, "docstatus": 1},
			["name", "posting_date"],
			as_dict=True,
		)
		if not invoice:
			create_unicommerce_log(
				status="Invalid",
				message=(
					f"No submitted sales invoice found for shipping package {package_code}, "
					f"skipped RTO return on order {so_data['code']}"
				),
				method="ecommerce_integrations.unicommerce.cancellation_and_returns.sync_rto_returns",
			)
			continue
		# Get return creation date and full Return API response (RTO uses shipment code)
		if not client:
			create_unicommerce_log(
				status="Invalid",
				message=f"Client not provided for RTO return {package_code}",
				method="ecommerce_integrations.unicommerce.cancellation_and_returns.sync_rto_returns",
			)
			continue
		return_timestamp, return_details = get_return_date_from_package(
			client, shipment_code=package_code, facility_code=facility_code
		)

		# Use return API date - no fallback to invoice date
		if not return_timestamp:
			create_unicommerce_log(
				status="Invalid",
				message=f"Unable to get return date from Return API for {package_code}",
				method="ecommerce_integrations.unicommerce.cancellation_and_returns.sync_rto_returns",
			)
			continue
		posting_date = get_unicommerce_date(return_timestamp)
		# isolated so a failure (eg. closed period) doesn't skip the remaining packages
		try:
			credit_note = create_credit_note(invoice.name, posting_date=posting_date)
			credit_note.save()

			create_unicommerce_log(
				status="Success",
				message="RTO credit note created successfully",
				method="ecommerce_integrations.unicommerce.cancellation_and_returns.sync_rto_returns",
				request_data=return_details,  # Full Unicommerce Return API response
			)
		except Exception as e:
			create_unicommerce_log(
				status="Error",
				exception=e,
				make_new=True,
				message=(
					f"Failed to create RTO credit note for shipping package {package_code} "
					f"on order {so_data['code']}"
				),
				method="ecommerce_integrations.unicommerce.cancellation_and_returns.sync_rto_returns",
			)


def get_return_warehouse(facility_code):
	return frappe.db.get_value(
		"Unicommerce Warehouses", {"unicommerce_facility_code": facility_code}, "return_warehouse"
	)


def get_return_date_from_package(client, return_code=None, facility_code=None, shipment_code=None):
	"""Get accurate return timestamp from Unicommerce Return API.

	Uses the Get Return API to fetch returnSaleOrderValue.returnCreatedDate
	which represents when the return was actually created.

	Args:
		client: UnicommerceAPIClient instance
		return_code: Reverse pickup code for CIR returns
		facility_code: Facility code for Return API header (required)
		shipment_code: Shipping package code for RTO returns

	Returns:
		tuple: (timestamp in milliseconds, return_details) or (None, None)
	"""
	try:
		return_details = client.get_return_details(
			reverse_pickup_code=return_code,
			shipment_code=shipment_code,
			facility_code=facility_code,
		)

		if return_details and return_details.get("returnSaleOrderValue"):
			# Return API gives datetime string, need to convert to timestamp
			return_created_str = return_details["returnSaleOrderValue"].get("returnCreatedDate")
			if return_created_str:
				# Convert datetime string to timestamp (milliseconds)
				from datetime import datetime

				dt = datetime.strptime(return_created_str, "%Y-%m-%d %H:%M:%S")
				timestamp = int(dt.timestamp() * 1000)
				return timestamp, return_details

	except Exception as e:
		code = return_code or shipment_code
		frappe.log_error(f"Failed to fetch return details for {code}: {e}")

	return None, None


def create_credit_note(invoice_name, posting_date=None):
	credit_note = make_sales_return(invoice_name)
	facility_code = credit_note.get(FACILITY_CODE_FIELD)
	return_warehouse = get_return_warehouse(facility_code)

	# Set posting date for backfilled returns so they land in the correct period
	if posting_date:
		credit_note.set_posting_time = 1
		credit_note.posting_date = posting_date

	for item in credit_note.items:
		item.warehouse = return_warehouse or item.warehouse

	for tax in credit_note.taxes:
		if hasattr(tax, "item_wise_tax_detail") and tax.item_wise_tax_detail:
			tax.item_wise_tax_detail = json.loads(tax.item_wise_tax_detail)
			for _item, tax_distribution in tax.item_wise_tax_detail.items():
				tax_distribution[1] *= -1
			tax.item_wise_tax_detail = json.dumps(tax.item_wise_tax_detail)

	return credit_note


def check_and_update_customer_initiated_returns(orders, client: UnicommerceAPIClient) -> None:
	"""Create credit notes for customer-initiated returns on recently updated orders."""

	recently_changed_orders = _filter_recent_orders(orders)

	for order in recently_changed_orders:
		so_data = client.get_sales_order(order["code"])
		if not so_data:
			continue
		sync_customer_initiated_returns(so_data, client)


def sync_customer_initiated_returns(so_data, client=None):
	customer_returns = [r for r in so_data.get("returns", []) if r["type"] == "Customer Returned"]
	if not customer_returns:
		return

	for customer_return in customer_returns:
		if frappe.db.exists("Sales Invoice", {RETURN_CODE_FIELD: customer_return["code"]}):
			continue

		# isolated so a failure (eg. closed period) doesn't skip the remaining returns
		try:
			create_cir_credit_note(so_data, customer_return, client)
		except Exception as e:
			create_unicommerce_log(
				status="Error",
				exception=e,
				make_new=True,
				message=(
					f"Failed to create credit note for return {customer_return['code']} "
					f"on order {so_data['code']}"
				),
				method="ecommerce_integrations.unicommerce.cancellation_and_returns.sync_customer_initiated_returns",
			)


def _get_invoice_for_return(order_code, returned_so_items):
	"""Find the invoice containing the returned items.

	An order with multiple packages has multiple invoices. Picking the wrong one
	leaves an empty credit note after _handle_partial_returns strips items.
	"""
	invoices = frappe.get_all(
		"Sales Invoice",
		filters={ORDER_CODE_FIELD: order_code, "is_return": 0, "docstatus": 1},
		pluck="name",
	)
	if len(invoices) <= 1:
		return invoices[0] if invoices else None

	# single query for all invoices, else one per invoice
	all_si_items = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": ["in", invoices]},
		fields=["parent", "so_detail"],
	)

	invoice_items = {}
	for item in all_si_items:
		invoice_items.setdefault(item["parent"], set()).add(item["so_detail"])

	for invoice_name in invoices:
		si_so_details = invoice_items.get(invoice_name, set())
		if returned_so_items and returned_so_items <= si_so_details:
			return invoice_name


def create_cir_credit_note(so_data, return_data, client=None):
	sales_order_name = frappe.db.get_value("Sales Order", {ORDER_CODE_FIELD: so_data["code"]})
	if not sales_order_name:
		create_unicommerce_log(
			status="Invalid",
			message=f"No sales order found for {so_data['code']}, skipped return {return_data['code']}",
			method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		)
		return
	so = frappe.get_doc("Sales Order", sales_order_name)

	# Get items from SO which are returned, map SO item -> SI item with linked rows.
	so_item_code_map = {item.get(ORDER_ITEM_CODE_FIELD): item.name for item in so.items}

	returned_so_codes = [item.get("saleOrderItemCode") for item in return_data.get("returnItems") or []]
	returned_so_items = {so_item_code_map.get(code) for code in returned_so_codes} - {None}

	invoice_name = _get_invoice_for_return(so_data["code"], returned_so_items)
	if not invoice_name:
		# no invoice, or the returned rows span more than one package's invoice
		create_unicommerce_log(
			status="Invalid",
			message=f"No sales invoice found for order {so_data['code']}, skipped return {return_data['code']}",
			method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		)
		return
	si = frappe.get_doc("Sales Invoice", invoice_name)
	so_si_item_map = {item.so_detail: item.name for item in si.items}

	facility_code = si.get(FACILITY_CODE_FIELD)

	# Initialize client if not provided
	if not client:
		client = UnicommerceAPIClient()

	return_timestamp, return_details = get_return_date_from_package(
		client, return_code=return_data["code"], facility_code=facility_code
	)

	# Use shipping package return date
	if not return_timestamp:
		create_unicommerce_log(
			status="Invalid",
			message=f"Unable to get return date from Return API for {return_data['code']}",
			method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		)
		return
	posting_date = get_unicommerce_date(return_timestamp)

	credit_note = create_credit_note(si.name, posting_date=posting_date)

	# return code is what the dedupe check looks up, else every run adds a draft
	credit_note.set(RETURN_CODE_FIELD, return_data["code"])
	credit_note.set(TRACKING_CODE_FIELD, return_data.get("trackingNumber"))
	credit_note.set(SHIPPING_PROVIDER_CODE, return_data.get("shippingProvider"))

	returned_si_items = [so_si_item_map.get(so_item) for so_item in returned_so_items]

	if set(returned_si_items) != set(so_si_item_map.values()):
		_handle_partial_returns(credit_note, returned_si_items)

	credit_note.save()

	create_unicommerce_log(
		status="Success",
		message="Customer return credit note created successfully",
		method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		request_data=return_details,  # Full Unicommerce Return API response
	)

	return credit_note


def _handle_partial_returns(credit_note, returned_items: list[str]) -> None:
	"""Remove non-returned items from credit note and update taxes."""

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
		if not (hasattr(tax, "item_wise_tax_detail") and tax.item_wise_tax_detail):
			continue

		item_wise_tax_detail = json.loads(tax.item_wise_tax_detail)
		new_tax_amt = 0.0

		for item_code, tax_distribution in item_wise_tax_detail.items():
			# item_code: [rate, amount]
			if not tax_distribution[1]:
				# Ignore 0 values
				continue

			# the breakup can name an item that isn't on this credit note
			original_qty = item_code_to_qty_map.get(item_code) or 0.0
			if not original_qty:
				tax_distribution[1] = 0.0
				continue

			return_percent = returned_qty_map.get(item_code, 0.0) / original_qty
			tax_distribution[1] *= return_percent
			new_tax_amt += tax_distribution[1]

		tax.tax_amount = new_tax_amt
		tax.item_wise_tax_detail = json.dumps(item_wise_tax_detail)
