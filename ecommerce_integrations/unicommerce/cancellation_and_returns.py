import json
from collections import defaultdict
from datetime import date, datetime

import frappe
from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return
from erpnext.accounts.services.child_item_update import update_child_qty_rate
from frappe.query_builder.functions import Abs, Sum
from frappe.utils import cint, flt, now_datetime, parse_json

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	IS_CHARGE_ROW_FIELD,
	ORDER_CODE_FIELD,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	RETURN_CODE_FIELD,
	SETTINGS_DOCTYPE,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date
from ecommerce_integrations.utils.taxation import copy_item_wise_tax_details


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
			["name", "posting_date", FACILITY_CODE_FIELD],
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
			client,
			shipment_code=package_code,
			facility_code=facility_code or invoice.get(FACILITY_CODE_FIELD),
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

	copy_item_wise_tax_details(credit_note, invoice_name, source_doctype="Sales Invoice")
	for row in credit_note.get("_item_wise_tax_details") or []:
		row.amount *= -1

	facility_code = credit_note.get(FACILITY_CODE_FIELD)
	return_warehouse = get_return_warehouse(facility_code)

	# Set posting date for backfilled returns so they land in the correct period
	if posting_date:
		credit_note.set_posting_time = 1
		credit_note.posting_date = posting_date

	for item in credit_note.items:
		item.warehouse = return_warehouse or item.warehouse

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


def _get_invoice_for_return(order_code, returned_so_items, returned_item_codes=None):
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
		fields=["name", "parent", "so_detail", "item_code", "qty"],
	)

	invoice_rows = defaultdict(set)
	invoice_item_codes = defaultdict(set)
	invoice_items = defaultdict(list)
	for item in all_si_items:
		invoice_rows[item["parent"]].add(item["so_detail"])
		invoice_item_codes[item["parent"]].add(item["item_code"])
		invoice_items[item["parent"]].append(item)

	for invoice_name in invoices:
		if returned_so_items and returned_so_items <= invoice_rows[invoice_name]:
			return invoice_name

	# invoices created without a warehouse allocation carry no row links at all:
	# match them by item code, after the linked ones never matched
	candidates = [
		invoice_name
		for invoice_name in invoices
		if invoice_rows[invoice_name] == {None}
		and returned_item_codes
		and returned_item_codes <= invoice_item_codes[invoice_name]
	]
	# two packages of one order can carry the same SKU: an invoice whose matching
	# rows are already refunded in full belongs to another package's return
	if len(candidates) > 1:
		return next(
			(
				invoice_name
				for invoice_name in candidates
				if _has_unrefunded_rows(invoice_name, invoice_items[invoice_name], returned_item_codes)
			),
			None,
		)
	return candidates[0] if candidates else None


def _get_refunded_qty_by_invoice_row(invoice_name: str, for_update: bool = True) -> dict[str, float]:
	"""Quantity already credited on each invoice row by non-cancelled credit notes.

	Rows are locked only on MariaDB; Postgres forbids FOR UPDATE with GROUP BY.
	for_update=False is used while only comparing invoices to pick one.
	"""

	sales_invoice = frappe.qb.DocType("Sales Invoice")
	sales_invoice_item = frappe.qb.DocType("Sales Invoice Item")

	refunded_rows = (
		frappe.qb.from_(sales_invoice_item)
		.inner_join(sales_invoice)
		.on(sales_invoice_item.parent == sales_invoice.name)
		.select(
			sales_invoice_item.sales_invoice_item,
			Abs(Sum(sales_invoice_item.qty)).as_("refunded_qty"),
		)
		.where(
			(sales_invoice.return_against == invoice_name)
			& (sales_invoice.is_return == 1)
			& (sales_invoice.docstatus < 2)
			& sales_invoice_item.sales_invoice_item.isnotnull()
		)
		.groupby(sales_invoice_item.sales_invoice_item)
	)

	if for_update and frappe.db.db_type == "mariadb":
		refunded_rows = refunded_rows.for_update()

	refunded = refunded_rows.run(as_dict=True)

	return {row.sales_invoice_item: row.refunded_qty for row in refunded}


def _has_unrefunded_rows(invoice_name, invoice_items, returned_item_codes) -> bool:
	"""True when the invoice still has rows for the returned items to credit."""
	refunded = _get_refunded_qty_by_invoice_row(invoice_name, for_update=False)
	return any(
		refunded.get(item["name"], 0) < item["qty"]
		for item in invoice_items
		if item["item_code"] in returned_item_codes
	)


def _is_charge_row(item, charge_item_codes) -> bool:
	"""A charge row is marked on the invoice when it is billed; the configured
	charge items only cover invoices created before the mark existed."""
	return item.get(IS_CHARGE_ROW_FIELD) or getattr(item, "item_code", None) in charge_item_codes


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
	so_item_code_by_name = {item.name: getattr(item, "item_code", None) for item in so.items}

	returned_qty_by_so_item = defaultdict(int)
	for return_item in return_data.get("returnItems") or []:
		if so_item := so_item_code_map.get(return_item.get("saleOrderItemCode")):
			returned_qty_by_so_item[so_item] += cint(return_item.get("quantity") or 1)

	returned_item_codes = {so_item_code_by_name[so_item] for so_item in returned_qty_by_so_item}
	invoice_name = _get_invoice_for_return(so_data["code"], set(returned_qty_by_so_item), returned_item_codes)
	if not invoice_name:
		# no invoice, or the returned rows span more than one package's invoice
		create_unicommerce_log(
			status="Invalid",
			message=f"No sales invoice found for order {so_data['code']}, skipped return {return_data['code']}",
			method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		)
		return
	si = frappe.get_doc("Sales Invoice", invoice_name, for_update=True)

	from ecommerce_integrations.unicommerce.invoice import _get_charge_items

	charge_item_codes = set(_get_charge_items(frappe.get_cached_doc(SETTINGS_DOCTYPE)).values())

	so_si_items_map = defaultdict(list)
	rows_by_item_code = defaultdict(list)
	for item in si.items:
		if item.so_detail:
			so_si_items_map[item.so_detail].append(item)
		elif not _is_charge_row(item, charge_item_codes):
			rows_by_item_code[getattr(item, "item_code", None)].append(item)

	facility_code = si.get(FACILITY_CODE_FIELD)

	if not client:
		create_unicommerce_log(
			status="Invalid",
			message=f"Client not provided for return {return_data['code']}",
			method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		)
		return

	return_timestamp, return_details = get_return_date_from_package(
		client, return_code=return_data["code"], facility_code=facility_code
	)

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

	previously_refunded_qty = _get_refunded_qty_by_invoice_row(si.name)

	refunded_qty_by_row: dict[str, float] = {}
	for so_item_name, returned_qty in returned_qty_by_so_item.items():
		rows = so_si_items_map.get(so_item_name)
		if not rows:
			rows = rows_by_item_code.get(so_item_code_by_name.get(so_item_name), ())
		units_left = returned_qty
		for row in rows:
			if units_left <= 0:
				break
			unrefunded = (
				row.qty - previously_refunded_qty.get(row.name, 0) - refunded_qty_by_row.get(row.name, 0)
			)
			if unrefunded <= 0:
				continue
			credited = min(units_left, unrefunded)
			refunded_qty_by_row[row.name] = refunded_qty_by_row.get(row.name, 0) + credited
			units_left -= credited

	# refund the charges only once: on the return that fully returns the invoice
	product_items = [
		item for item in si.items if item.so_detail or not _is_charge_row(item, charge_item_codes)
	]
	if product_items and all(
		previously_refunded_qty.get(item.name, 0) + refunded_qty_by_row.get(item.name, 0) >= item.qty
		for item in product_items
	):
		for item in si.items:
			if _is_charge_row(item, charge_item_codes) and item.name not in previously_refunded_qty:
				refunded_qty_by_row[item.name] = item.qty

	is_full_return = all(refunded_qty_by_row.get(item.name) == item.qty for item in si.items)
	if not is_full_return:
		_handle_partial_returns(credit_note, refunded_qty_by_row)

	credit_note.save()

	create_unicommerce_log(
		status="Success",
		message="Customer return credit note created successfully",
		method="ecommerce_integrations.unicommerce.cancellation_and_returns.create_cir_credit_note",
		request_data=return_details,
	)

	return credit_note


def _carries_tax_account(item, account_head) -> bool:
	"""True when the item's stored tax rates include this tax account."""
	rates = parse_json(getattr(item, "item_tax_rate", None)) or {}
	return account_head in rates


def _handle_partial_returns(credit_note, refunded_qty_by_row: dict[str, float]) -> None:
	"""Remove non-returned items, reduce partially returned ones, and update taxes.

	Charge rows are added by the caller, only on the fully returning credit note.
	"""

	def net_amount(item):
		return abs(flt(getattr(item, "amount", None)) or flt(item.qty) * flt(item.rate))

	breakup = getattr(credit_note, "_item_wise_tax_details", None) or []

	# each row's billed qty before it is reduced, keyed by its invoice row
	original_qty_by_row = {item.sales_invoice_item: abs(item.qty) for item in credit_note.items}
	total_amount = sum(net_amount(item) for item in credit_note.items)
	# each account's billed amount, snapshotted before rows are reduced in place
	taxed_total_by_account = defaultdict(float)
	for item in credit_note.items:
		for account_head in parse_json(getattr(item, "item_tax_rate", None)) or {}:
			taxed_total_by_account[account_head] += net_amount(item)

	credit_note.items = [item for item in credit_note.items if item.sales_invoice_item in refunded_qty_by_row]

	# credit only the returned qty, keeping the row's sign
	for item in credit_note.items:
		refunded_qty = refunded_qty_by_row[item.sales_invoice_item]
		item.qty = -refunded_qty if item.qty < 0 else refunded_qty
		item.amount = flt(item.qty * item.rate)

	if not breakup:
		returned_amount = sum(net_amount(item) for item in credit_note.items)
		if not total_amount:
			return
		for tax in credit_note.taxes:
			account_head = getattr(tax, "account_head", None)
			taxed_total = taxed_total_by_account.get(account_head)
			if taxed_total:
				taxed_returned = sum(
					net_amount(item) for item in credit_note.items if _carries_tax_account(item, account_head)
				)
				share = taxed_returned / taxed_total
			else:
				share = returned_amount / total_amount
			tax.tax_amount = flt(flt(tax.tax_amount) * share)
		return

	return_percent_by_row = {
		item.sales_invoice_item: abs(item.qty) / original_qty_by_row[item.sales_invoice_item]
		for item in credit_note.items
		if original_qty_by_row.get(item.sales_invoice_item)
	}

	# the breakup is parent-level rows, one per invoice row and tax
	scaled_rows = []
	for tax in credit_note.taxes:
		tax_rows = [row for row in breakup if row.tax is tax]
		# a tax row without a breakup is left alone
		if not tax_rows:
			continue
		tax_amount = 0.0
		for row in tax_rows:
			# each row keeps its own tax; a removed item's rows are dropped
			return_percent = return_percent_by_row.get(row.item.sales_invoice_item)
			if return_percent is None:
				continue
			row.amount = flt(row.amount) * return_percent
			tax_amount += row.amount
			scaled_rows.append(row)
		tax.tax_amount = tax_amount
	credit_note._item_wise_tax_details = scaled_rows
