# Copyright (c) 2022, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.order import get_tax_account_description, get_tax_account_head
from ecommerce_integrations.shopify.product import get_item_code
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_sales_return(payload, request_id=None):
	return_data = payload
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	sales_invoice = frappe.db.get_value(
		"Sales Invoice",
		filters={ORDER_ID_FIELD: cstr(return_data["order_id"]), "is_return": 0, "docstatus": 1},
	)
	if not sales_invoice:
		create_shopify_log(
			status="Invalid",
			message="Sales Invoice not found for syncing sales return.",
			request_data=return_data,
		)
		return

	try:
		setting = frappe.get_doc(SETTING_DOCTYPE)
		return_items, restocked_items, taxes = get_return_items_and_taxes(
			return_data, setting.cost_center
		)
		return_inv = make_return_inv(return_items, taxes, sales_invoice)
		return_inv.insert().submit()
		if return_data.get("transactions"):
			make_payment_against_sales_return(
				setting, return_inv, flt(return_data["transactions"][0]["amount"])
			)
		create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def make_return_inv(return_items, taxes, source_name: str, target_doc=None):
	from frappe.model.mapper import get_mapped_doc

	def set_missing_values(source, target):
		doc = frappe.get_doc(target)
		doc.is_return = 1
		doc.return_against = source.name
		doc.taxes = []
		for tax in taxes:
			doc.append("taxes", tax)

	def update_item(source_doc, target_doc, source_parent):
		target_doc.qty = -1 * flt(return_items.get(target_doc.item_code, 1))
		target_doc.stock_qty = flt(target_doc.qty * target_doc.conversion_factor)

		target_doc.so_detail = source_doc.so_detail
		target_doc.sales_order = source_doc.sales_order

		target_doc.dn_detail = source_doc.dn_detail
		target_doc.delivery_note = source_doc.delivery_note

		target_doc.expense_account = source_doc.expense_account
		target_doc.sales_invoice_item = source_doc.name

	doclist = get_mapped_doc(
		"Sales Invoice",
		source_name,
		{
			"Sales Invoice": {"doctype": "Sales Invoice", "validation": {"docstatus": ["=", 1],},},
			"Sales Invoice Item": {
				"doctype": "Sales Invoice Item",
				"condition": lambda doc: doc.item_code in return_items.keys(),
				"postprocess": update_item,
			},
		},
		target_doc,
		set_missing_values,
	)

	return doclist


def get_return_items_and_taxes(shopify_order, cost_center):
	taxes = []
	refund_line_items = shopify_order.get("refund_line_items")
	return_items = {}
	restocked_items = {}

	for d in refund_line_items:
		line_item = d.get("line_item")
		if not line_item:
			continue

		item_code = get_item_code(line_item)

		return_items[item_code] = d.get("quantity", 1)
		if d.get("restock_type") == "restock":
			restocked_items[item_code] = d.get("quantity", 1)

		for tax in line_item.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax),
					"description": (
						f"{get_tax_account_description(tax) or tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": -(tax.get("rate") * d.get("subtotal")),
					"included_in_print_rate": 0,
					"cost_center": cost_center,
					"item_wise_tax_detail": json.dumps(
						{item_code: [flt(tax.get("rate")) * 100, -(flt(d.get("subtotal")))]}
					),
					"dont_recompute_tax": 1,
				}
			)

	return return_items, restocked_items, taxes


def make_payment_against_sales_return(setting, doc, paid_amount):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(
		"Sales Invoice", doc.return_against, bank_account=setting.cash_bank_account
	)
	payment_entry.paid_amount = paid_amount
	payment_entry.set_gain_or_loss(
		account_details={
			"account": frappe.get_cached_value("Company", payment_entry.company, "write_off_account"),
			"cost_center": setting.cost_center,
			"amount": flt((doc.rounded_total or doc.grand_total) + paid_amount),
		}
	)
	payment_entry.set_difference_amount()
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = doc.posting_date or nowdate()
	payment_entry.reference_date = doc.posting_date or nowdate()
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()
