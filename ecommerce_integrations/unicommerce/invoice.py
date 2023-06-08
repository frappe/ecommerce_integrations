import base64
import json
from collections import defaultdict
from typing import Any, Dict, List, NewType, Optional

import frappe
import requests
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe import _
from frappe.utils import cint, flt, nowdate
from frappe.utils.file_manager import save_file

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	IS_COD_CHECKBOX,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	ORDER_INVOICE_STATUS_FIELD,
	SETTINGS_DOCTYPE,
	SHIPPING_METHOD_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PACKAGE_STATUS_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.order import get_taxes
from ecommerce_integrations.unicommerce.utils import (
	create_unicommerce_log,
	get_unicommerce_date,
	remove_non_alphanumeric_chars,
)

JsonDict = Dict[str, Any]
SOCode = NewType("SOCode", str)

# TypedDict
# 	sales_order_row: str
# 	item_code: str
# 	warehouse: str
# 	batch_no: str
ItemWHAlloc = Dict[str, str]


WHAllocation = Dict[SOCode, List[ItemWHAlloc]]

INVOICED_STATE = ["PACKED", "READY_TO_SHIP", "DISPATCHED", "MANIFESTED", "SHIPPED", "DELIVERED"]


@frappe.whitelist()
def generate_unicommerce_invoices(
	sales_orders: List[SOCode], warehouse_allocation: Optional[WHAllocation] = None
):
	"""Request generation of invoice to Unicommerce and sync that invoice.

	1. Get shipping package details using get_sale_order
	2. Ask for invoice generation
	                - marketplace - create_invoice_and_label_by_shipping_code
	                - self-shipped - create_invoice_and_assign_shipper

	3. Sync invoice.

	args:
	                sales_orders: list of sales order codes to invoice.
	                warehouse_allocation: If warehouse is changed while shipping / non-group warehouse is to be assigned then this parameter is required.

	        Example of warehouse_allocation:

	        {
	          "SO0042": [
	                  {
	                        "item_code": "SKU",
	                        # "qty": 1, always assumed to be 1 for Unicommerce orders.
	                        "warehouse": "Stores - WP",
	                        "sales_order_row": "5hh123k1", `name` of SO child table row
	                  },
	                  {
	                         "item_code": "SKU2",
	                         # "qty": 1,
	                         "warehouse": "Stores - WP",
	                         "sales_order_row": "5hh123k1", `name` of SO child table row
	                  },
	           ],
	           "SO0101": [
	                  {
	                         "item_code": "SKU3",
	                         # "qty": 1
	                         "warehouse": "Stores - WP",
	                         "sales_order_row": "5hh123k1", `name` of SO child table row
	                  },
	           ]
	        }
	"""

	if isinstance(sales_orders, str):
		sales_orders = json.loads(sales_orders)

	if isinstance(warehouse_allocation, str):
		warehouse_allocation = json.loads(warehouse_allocation)

	if warehouse_allocation:
		_validate_wh_allocation(warehouse_allocation)

	if len(sales_orders) == 1:
		# perform in web request
		bulk_generate_invoices(sales_orders, warehouse_allocation)
	else:
		# send to background job

		log = create_unicommerce_log(
			method="ecommerce_integrations.unicommerce.invoice.bulk_generate_invoices",
			request_data={"sales_orders": sales_orders, "warehouse_allocation": warehouse_allocation},
		)

		frappe.enqueue(
			method="ecommerce_integrations.unicommerce.invoice.bulk_generate_invoices",
			queue="long",
			timeout=max(1500, len(sales_orders) * 30),
			sales_orders=sales_orders,
			warehouse_allocation=warehouse_allocation,
			request_id=log.name,
		)


def bulk_generate_invoices(
	sales_orders: List[SOCode],
	warehouse_allocation: Optional[WHAllocation] = None,
	request_id=None,
	client=None,
):
	if client is None:
		client = UnicommerceAPIClient()
	frappe.flags.request_id = request_id  #  for auto-picking current log

	update_invoicing_status(sales_orders, "Queued")

	failed_orders = []
	for so_code in sales_orders:
		try:
			so = frappe.get_doc("Sales Order", so_code)
			channel = so.get(CHANNEL_ID_FIELD)
			channel_config = frappe.get_cached_doc("Unicommerce Channel", channel)
			wh_allocation = warehouse_allocation.get(so_code) if warehouse_allocation else None
			_generate_invoice(client, so, channel_config, warehouse_allocation=wh_allocation)
		except Exception as e:
			create_unicommerce_log(status="Failure", exception=e, rollback=True, make_new=True)
			failed_orders.append(so_code)

	_log_invoice_generation(sales_orders, failed_orders)


def _log_invoice_generation(sales_orders, failed_orders):

	failed_orders = set(failed_orders)
	failed_orders.update(_get_orders_with_missing_invoice(sales_orders))
	successful_orders = list(set(sales_orders) - set(failed_orders))

	percent_success = len(successful_orders) / len(sales_orders)

	failure_message = "\n".join(
		[
			f"generate invoices: {percent_success:.3%} invoices successful\n",
			f"Failred orders = {', '.join(failed_orders)}",
			f"Requested orders = {', '.join(sales_orders)}",
		]
	)

	update_invoicing_status(failed_orders, "Failed")
	update_invoicing_status(successful_orders, "Success")

	status = {0.0: "Failure", 100.0: "Success"}.get(percent_success) or "Partial Success"
	create_unicommerce_log(status=status, message=failure_message)


def _get_orders_with_missing_invoice(sales_orders):
	missing_invoices = set()

	for order in sales_orders:
		uni_so_code = frappe.db.get_value("Sales Order", order, ORDER_CODE_FIELD)
		invoice_exists = frappe.db.exists("Sales Invoice", {ORDER_CODE_FIELD: uni_so_code})
		if not invoice_exists:
			missing_invoices.add(order)

	return missing_invoices


def update_invoicing_status(sales_orders: List[str], status: str) -> None:
	if not sales_orders:
		return

	frappe.db.sql(
		f"""update `tabSales Order`
			set {ORDER_INVOICE_STATUS_FIELD} = %s
			where name in %s""",
		(status, sales_orders),
	)


def _validate_wh_allocation(warehouse_allocation: WHAllocation):
	"""Validate that provided warehouse allocation is exactly sufficient for fulfilling the orders."""

	if not warehouse_allocation:
		return

	so_codes = list(warehouse_allocation.keys())
	so_item_data = frappe.db.sql(
		"""
			select item_code, sum(qty) as qty, parent as sales_order
			from `tabSales Order Item`
			where
				parent in %s
			group by parent, item_code""",
		(so_codes,),
		as_dict=True,
	)

	expected_item_qty = {}
	for item in so_item_data:
		expected_item_qty.setdefault(item.sales_order, {})[item.item_code] = item.qty

	for order, item_details in warehouse_allocation.items():
		item_wise_qty = defaultdict(int)
		for item in item_details:
			item_wise_qty[item["item_code"]] += 1

		# group item details for total qty
		for item_code, total_qty in item_wise_qty.items():
			expected_qty = expected_item_qty.get(order, {}).get(item_code)
			if abs(total_qty - expected_qty) > 0.1:
				msg = _("Mismatch in quantity for order {}, item {} exepcted {} qty, received {}").format(
					order, item_code, expected_qty, total_qty
				)
				frappe.throw(msg)


def _generate_invoice(
	client: UnicommerceAPIClient, erpnext_order, channel_config, warehouse_allocation=None
):
	unicommerce_so_code = erpnext_order.get(ORDER_CODE_FIELD)

	so_data = client.get_sales_order(unicommerce_so_code)
	shipping_packages = [d["code"] for d in so_data["shippingPackages"] if d["status"] == "CREATED"]

	# TODO:  check if already generated by erpnext invoice unsyced
	facility_code = erpnext_order.get(FACILITY_CODE_FIELD)

	package_invoice_response_map = {}

	for package in shipping_packages:
		response = None
		if cint(channel_config.shipping_handled_by_marketplace):
			response = client.create_invoice_and_label_by_shipping_code(
				shipping_package_code=package, facility_code=facility_code
			)
		else:
			response = client.create_invoice_and_assign_shipper(
				shipping_package_code=package, facility_code=facility_code
			)
		package_invoice_response_map[package] = response

	_fetch_and_sync_invoice(
		client,
		unicommerce_so_code,
		erpnext_order.name,
		facility_code,
		warehouse_allocation=warehouse_allocation,
		invoice_responses=package_invoice_response_map,
	)


def _fetch_and_sync_invoice(
	client: UnicommerceAPIClient,
	unicommerce_so_code,
	erpnext_so_code,
	facility_code,
	warehouse_allocation=None,
	invoice_responses=None,
):
	"""Use the invoice generation response to fetch actual invoice and sync them to ERPNext.

	args:
	                invoice_response: response returned by either of two invoice generation methods
	"""

	so_data = client.get_sales_order(unicommerce_so_code)
	shipping_packages = [
		d["code"] for d in so_data["shippingPackages"] if d["status"] in INVOICED_STATE
	]

	for package in shipping_packages:
		invoice_response = invoice_responses.get(package) or {}
		invoice_data = client.get_sales_invoice(package, facility_code)["invoice"]
		label_pdf = fetch_label_pdf(
			package, invoice_response, client=client, facility_code=facility_code
		)
		create_sales_invoice(
			invoice_data,
			erpnext_so_code,
			update_stock=1,
			shipping_label=label_pdf,
			warehouse_allocations=warehouse_allocation,
			invoice_response=invoice_response,
			so_data=so_data,
		)


def create_sales_invoice(
	si_data: JsonDict,
	so_code: str,
	update_stock=0,
	submit=True,
	shipping_label=None,
	warehouse_allocations=None,
	invoice_response=None,
	so_data: Optional[JsonDict] = None,
):
	"""Create ERPNext Sales Invcoice using Unicommerce sales invoice data and related Sales order.

	Sales Order is required to fetch missing order in the Sales Invoice.
	"""
	if not invoice_response:
		invoice_response = {}
	if not so_data:
		so_data = {}
	so = frappe.get_doc("Sales Order", so_code)

	if so_data:
		fully_cancelled = update_cancellation_status(so_data, so)
		if fully_cancelled:
			create_unicommerce_log(status="Invalid", message="Sales order was cancelled before invoicing.")
			return

	channel = so.get(CHANNEL_ID_FIELD)
	facility_code = so.get(FACILITY_CODE_FIELD)

	existing_si = frappe.db.get_value("Sales Invoice", {INVOICE_CODE_FIELD: si_data["code"]})
	if existing_si:
		si = frappe.get_doc("Sales Invoice", existing_si)
		create_unicommerce_log(status="Invalid", message="Sales Invoice already exists, skipped")
		return si

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	channel_config = frappe.get_cached_doc("Unicommerce Channel", channel)

	uni_line_items = si_data["invoiceItems"]
	warehouse = settings.get_integration_to_erpnext_wh_mapping(all_wh=True).get(facility_code)

	shipping_package_code = si_data.get("shippingPackageCode")
	shipping_package_info = _get_shipping_package(so_data, shipping_package_code) or {}

	tracking_no = invoice_response.get("trackingNumber") or shipping_package_info.get(
		"trackingNumber"
	)
	shipping_provider_code = (
		invoice_response.get("shippingProviderCode")
		or shipping_package_info.get("shippingProvider")
		or shipping_package_info.get("shippingCourier")
	)
	shipping_package_status = shipping_package_info.get("status")

	si = make_sales_invoice(so.name)
	si_line_items = _get_line_items(
		uni_line_items, warehouse, so.name, channel_config.cost_center, warehouse_allocations
	)
	si.set("items", si_line_items)
	si.set("taxes", get_taxes(uni_line_items, channel_config))
	si.set(INVOICE_CODE_FIELD, si_data["code"])
	si.set(SHIPPING_PACKAGE_CODE_FIELD, shipping_package_code)
	si.set(SHIPPING_PROVIDER_CODE, shipping_provider_code)
	si.set(TRACKING_CODE_FIELD, tracking_no)
	si.set(IS_COD_CHECKBOX, so_data["cod"])
	si.set(SHIPPING_METHOD_FIELD, shipping_package_info.get("shippingMethod"))
	si.set(SHIPPING_PACKAGE_STATUS_FIELD, shipping_package_status)
	si.set(CHANNEL_ID_FIELD, channel)
	si.set_posting_time = 1
	si.posting_date = get_unicommerce_date(si_data["created"])
	si.transaction_date = si.posting_date
	si.naming_series = channel_config.sales_invoice_series or settings.sales_invoice_series
	si.delivery_date = so.delivery_date
	si.ignore_pricing_rule = 1
	si.update_stock = False if settings.delivery_note else update_stock
	si.flags.raw_data = si_data
	si.insert()

	_verify_total(si, si_data)

	attach_unicommerce_docs(
		sales_invoice=si.name,
		invoice=si_data.get("encodedInvoice"),
		label=shipping_label,
		invoice_code=si_data["code"],
		package_code=si_data.get("shippingPackageCode"),
	)

	item_warehouses = {d.warehouse for d in si.items}
	for wh in item_warehouses:
		if update_stock and cint(frappe.db.get_value("Warehouse", wh, "is_group")):
			# can't submit stock transaction where warehouse is group
			return si

	if submit:
		si.submit()

	if cint(channel_config.auto_payment_entry):
		make_payment_entry(si, channel_config, si.posting_date)

	return si


def attach_unicommerce_docs(
	sales_invoice: str,
	invoice: Optional[str],
	label: Optional[str],
	invoice_code: Optional[str],
	package_code: Optional[str],
) -> None:
	"""Attach invoice and label to specified sales invoice.

	Both invoice and label are base64 encoded PDFs.

	File names are generated using specified invoice and shipping package code."""

	invoice_code = remove_non_alphanumeric_chars(invoice_code)
	package_code = remove_non_alphanumeric_chars(package_code)

	if invoice:
		save_file(
			f"unicommerce-invoice-{invoice_code}.pdf",
			invoice,
			"Sales Invoice",
			sales_invoice,
			decode=True,
			is_private=1,
		)

	if label:
		save_file(
			f"unicommerce-label-{package_code}.pdf",
			label,
			"Sales Invoice",
			sales_invoice,
			decode=True,
			is_private=1,
		)


def _get_line_items(
	line_items,
	warehouse: str,
	so_code: str,
	cost_center: str,
	warehouse_allocations: Optional[WHAllocation] = None,
) -> List[Dict[str, Any]]:
	""" Invoice items can be different and are consolidated, hence recomputing is required """

	si_items = []
	for item in line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		for __ in range(cint(item["quantity"])):
			si_items.append(
				{
					"item_code": item_code,
					# Note: Discount is already removed from this price.
					"rate": item["unitPrice"],
					"qty": 1,
					"stock_uom": "Nos",
					"warehouse": warehouse,
					"cost_center": cost_center,
					"sales_order": so_code,
				}
			)

	if warehouse_allocations:
		return _assign_wh_and_so_row(si_items, warehouse_allocations, so_code)

	return si_items


def _assign_wh_and_so_row(line_items, warehouse_allocation: List[ItemWHAlloc], so_code: str):

	so_items = frappe.get_doc("Sales Order", so_code).items
	so_item_price_map = {d.name: d.rate for d in so_items}

	# remove cancelled items
	warehouse_allocation = [
		d for d in warehouse_allocation if d["sales_order_row"] in so_item_price_map
	]

	# update price
	for item in warehouse_allocation:
		item["rate"] = so_item_price_map.get(item["sales_order_row"])

	sort_key = lambda d: (d.get("item_code"), d.get("rate"))  # noqa

	warehouse_allocation.sort(key=sort_key)
	line_items.sort(key=sort_key)

	# update references
	for item, wh_alloc in zip(line_items, warehouse_allocation):
		item["so_detail"] = wh_alloc["sales_order_row"]
		item["warehouse"] = wh_alloc["warehouse"]
		item["batch_no"] = wh_alloc.get("batch_no")

	return line_items


def _verify_total(si, si_data) -> None:
	""" Leave a comment if grand total does not match unicommerce total"""
	if abs(si.grand_total - flt(si_data["total"])) > 0.5:
		si.add_comment(text=f"Invoice totals mismatch: Unicommerce reported total of {si_data['total']}")


def _get_shipping_package(si_data, package_code):
	if not package_code:
		return
	packages = si_data.get("shippingPackages") or []
	for package in packages:
		if package.get("code") == package_code:
			return package


def make_payment_entry(invoice, channel_config, invoice_posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(
		invoice.doctype, invoice.name, bank_account=channel_config.cash_or_bank_account
	)

	payment_entry.reference_no = invoice.get(ORDER_CODE_FIELD) or invoice.name
	payment_entry.posting_date = invoice_posting_date or nowdate()
	payment_entry.reference_date = invoice_posting_date or nowdate()

	payment_entry.insert(ignore_permissions=True)
	if channel_config.submit_payment_entry:
		payment_entry.submit()


def fetch_label_pdf(package, invoicing_response, client, facility_code):

	if invoicing_response and invoicing_response.get("shippingLabelLink"):
		link = invoicing_response.get("shippingLabelLink")
		return fetch_pdf_as_base64(link)
	else:
		return client.get_invoice_label(package, facility_code)


def fetch_pdf_as_base64(link):
	try:
		response = requests.get(link)
		response.raise_for_status()

		return base64.b64encode(response.content)
	except Exception:
		return


def update_cancellation_status(so_data, so) -> bool:
	"""Check and update cancellation status, if fully cancelled return True"""
	# fully cancelled
	if so_data.get("status") == "CANCELLED":
		so.cancel()
		return True

	# partial cancels
	from ecommerce_integrations.unicommerce.cancellation_and_returns import update_erpnext_order_items

	update_erpnext_order_items(so_data, so)


def on_submit(self, method=None):
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.is_enabled():
		return

	sales_order = self.get("items")[0].sales_order
	unicommerce_order_code = frappe.db.get_value("Sales Order", sales_order, "unicommerce_order_code")
	if unicommerce_order_code:
		attached_docs = frappe.get_all(
			"File",
			fields=["file_name"],
			filters={"attached_to_name": self.name, "file_name": ("like", "unicommerce%")},
			order_by="file_name",
		)
		url = frappe.get_all(
			"File",
			fields=["file_url"],
			filters={"attached_to_name": self.name, "file_name": ("like", "unicommerce%")},
			order_by="file_name",
		)
		pi_so = frappe.get_all(
			"Pick List Sales Order Details",
			fields=["name", "parent"],
			filters=[{"sales_order": sales_order, "docstatus": 0}],
		)
		for pl in pi_so:
			if not pl.parent or not frappe.db.exists("Pick List", pl.parent):
				continue
			if attached_docs:
				frappe.db.set_value(
					"Pick List Sales Order Details",
					pl.name,
					{
						"sales_invoice": self.name,
						"invoice_url": attached_docs[0].file_name,
						"invoice_pdf": url[0].file_url,
					},
				)
			else:
				frappe.db.set_value("Pick List Sales Order Details", pl.name, {"sales_invoice": self.name})


def on_cancel(self, method=None):
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.is_enabled():
		return

	results = frappe.db.get_all(
		"Pick List Sales Order Details", filters={"sales_invoice": self.name, "docstatus": 1}
	)
	if results:
		# self.flags.ignore_links = True
		ignored_doctypes = list(self.get("ignore_linked_doctypes", []))
		ignored_doctypes.append("Pick List")
		self.ignore_linked_doctypes = ignored_doctypes
