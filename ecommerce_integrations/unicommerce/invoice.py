from typing import Any, Dict, List

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils.file_manager import save_file

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	CHANNEL_TAX_ACCOUNT_FIELD_MAP,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	MODULE_NAME,
	SETTINGS_DOCTYPE,
	SHIPPING_PACKAGE_CODE_FIELD,
	TAX_FIELDS_MAPPING,
)
from ecommerce_integrations.unicommerce.order import _get_shipping_line
from ecommerce_integrations.unicommerce.utils import get_unicommerce_date

JsonDict = Dict[str, Any]


def create_sales_invoice(si_data: JsonDict, so_code: str):
	"""Create ERPNext Sales Invcoice using Unicommerce sales invoice data and related Sales order.

	Sales Order is required to fetch missing order in the Sales Invoice.
	"""
	so = frappe.get_doc("Sales Order", so_code)
	channel = so.get(CHANNEL_ID_FIELD)
	facility_code = so.get(FACILITY_CODE_FIELD)

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	channel_config = frappe.get_cached_doc("Unicommerce Channel", channel)

	line_items = si_data["invoiceItems"]
	warehouse = settings.get_integration_to_erpnext_wh_mapping().get(facility_code)

	si = make_sales_invoice(so.name)
	si.set("items", _get_line_items(line_items, warehouse, channel_config.cost_center))
	si.set("taxes", _get_tax_lines(line_items, channel_config))
	si.set(INVOICE_CODE_FIELD, si_data["code"])
	si.set(SHIPPING_PACKAGE_CODE_FIELD, si_data.get("shippingPackageCode"))
	si.set_posting_time = 1
	si.posting_date = get_unicommerce_date(si_data["created"])
	si.transaction_date = si.posting_date
	si.naming_series = channel_config.sales_invoice_series or settings.sales_order_series
	si.delivery_date = so.delivery_date
	si.ignore_pricing_rule = 1
	si.insert()

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


def _get_line_items(line_items, warehouse: str, cost_center: str) -> List[Dict[str, Any]]:
	""" Invoice items can be different and are consolidated, hence recomputing is required """

	so_items = []
	for item in line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME, integration_item_code=item["itemSku"]
		)
		so_items.append(
			{
				"item_code": item_code,
				# Note: Discount is already removed from this price.
				"rate": item["sellingPrice"],
				"qty": 1,
				"stock_uom": "Nos",
				"warehouse": warehouse,
				"cost_center": cost_center,
			}
		)
	return so_items


def _get_tax_lines(line_items, channel_config):
	tax_map = {tax_head: 0.0 for tax_head in TAX_FIELDS_MAPPING.keys()}

	tax_account_map = {
		tax_head: channel_config.get(account_field)
		for tax_head, account_field in CHANNEL_TAX_ACCOUNT_FIELD_MAP.items()
	}
	for item in line_items:
		for tax_head, unicommerce_field in TAX_FIELDS_MAPPING.items():
			tax_map[tax_head] += item.get(unicommerce_field) or 0.0

	taxes = []

	for tax_head, value in tax_map.items():
		if not value:
			continue
		taxes.append(
			{
				"charge_type": "Actual",
				"account_head": tax_account_map[tax_head],
				"tax_amount": value,
				"description": tax_head.upper(),
			}
		)

	taxes.extend(_get_shipping_line(line_items, channel_config))

	return taxes


def _verify_total(si, si_data) -> None:
	""" Leave a comment if grand total does not match unicommerce total"""
	if abs(si.grand_total - si_data["total"]) > 0.5:
		si.add_comment(text=f"Invoice totals mismatch: Unicommerce reported total of {si_data['total']}")
