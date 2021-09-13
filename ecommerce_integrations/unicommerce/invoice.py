from typing import Any, Dict, List

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils import cint, flt, nowdate
from frappe.utils.file_manager import save_file

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	SETTINGS_DOCTYPE,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.order import get_taxes
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date

JsonDict = Dict[str, Any]


def create_sales_invoice(si_data: JsonDict, so_code: str, update_stock=0, submit=True):
	"""Create ERPNext Sales Invcoice using Unicommerce sales invoice data and related Sales order.

	Sales Order is required to fetch missing order in the Sales Invoice.
	"""
	so = frappe.get_doc("Sales Order", so_code)
	channel = so.get(CHANNEL_ID_FIELD)
	facility_code = so.get(FACILITY_CODE_FIELD)

	existing_si = frappe.db.get_value("Sales Invoice", {INVOICE_CODE_FIELD: si_data["code"]})
	if existing_si:
		si = frappe.get_doc("Sales Invoice", existing_si)
		create_unicommerce_log(status="Invalid", message="Sales Invoice already exists, skipped")
		return si

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	channel_config = frappe.get_cached_doc("Unicommerce Channel", channel)

	line_items = si_data["invoiceItems"]
	warehouse = settings.get_integration_to_erpnext_wh_mapping(all_wh=True).get(facility_code)

	si = make_sales_invoice(so.name)
	si.set("items", _get_line_items(line_items, warehouse, so.name, channel_config.cost_center))
	si.set("taxes", get_taxes(line_items, channel_config))
	si.set(INVOICE_CODE_FIELD, si_data["code"])
	si.set(SHIPPING_PACKAGE_CODE_FIELD, si_data.get("shippingPackageCode"))
	si.set_posting_time = 1
	si.posting_date = get_unicommerce_date(si_data["created"])
	si.transaction_date = si.posting_date
	si.naming_series = channel_config.sales_invoice_series or settings.sales_order_series
	si.delivery_date = so.delivery_date
	si.ignore_pricing_rule = 1
	si.update_stock = update_stock
	si.insert()
	if submit:
		si.submit()

	_verify_total(si, si_data)

	if si_data.get("encodedInvoice"):
		# attach file to the sales invoice
		save_file(
			f"unicommerce-invoice-{si_data['code']}.pdf",
			si_data["encodedInvoice"],
			si.doctype,
			si.name,
			decode=True,
			is_private=1,
		)

	if cint(channel_config.auto_payment_entry):
		make_payment_entry(si, channel_config, si.posting_date)

	return si


def _get_line_items(
	line_items, warehouse: str, so_code: str, cost_center: str
) -> List[Dict[str, Any]]:
	""" Invoice items can be different and are consolidated, hence recomputing is required """

	si_items = []
	for item in line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		si_items.append(
			{
				"item_code": item_code,
				# Note: Discount is already removed from this price.
				"rate": item["unitPrice"],
				"qty": item["quantity"],
				"stock_uom": "Nos",
				"warehouse": warehouse,
				"cost_center": cost_center,
				"sales_order": so_code,
			}
		)
	return si_items


def _verify_total(si, si_data) -> None:
	""" Leave a comment if grand total does not match unicommerce total"""
	if abs(si.grand_total - flt(si_data["total"])) > 0.5:
		si.add_comment(text=f"Invoice totals mismatch: Unicommerce reported total of {si_data['total']}")


def make_payment_entry(invoice, channel_config, invoice_posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(
		invoice.doctype, invoice.name, bank_account=channel_config.cash_or_bank_account
	)

	payment_entry.reference_no = invoice.get(ORDER_CODE_FIELD) or invoice.name
	payment_entry.posting_date = invoice_posting_date or nowdate()
	payment_entry.reference_date = invoice_posting_date or nowdate()

	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()
