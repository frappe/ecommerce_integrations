# Copyright (c) 2022, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint, cstr, flt, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.order import get_tax_account_description, get_tax_account_head
from ecommerce_integrations.shopify.product import get_item_code
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_sales_return(payload, request_id=None):
	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id
	return_data = payload

	try:
		sales_invoice = frappe.db.get_value(
			"Sales Invoice",
			filters={ORDER_ID_FIELD: cstr(return_data["order_id"]), "is_return": 0, "docstatus": 1},
		)
		if sales_invoice:
			create_sales_return(return_data, setting, sales_invoice)
			create_shopify_log(status="Success")
		else:
			create_shopify_log(status="Invalid", message="Sales Invoice not found for syncing sales return.")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_sales_return(return_data, setting, sales_invoice):
	return_items, restocked_items, taxes = get_return_items_and_taxes(
		return_data, setting.cost_center
	)

	if cint(setting.sync_sales_return):
		return_inv = make_return_document("Sales Invoice", return_items, taxes, sales_invoice)
		return_inv.flags.ignore_mandatory = True
		return_inv.naming_series = (
			setting.return_invoice_series
			or setting.sales_invoice_series
			or "SI-RET-Shopify-"
		)
		return_inv.insert().submit()

		if return_data.get("transactions"):
			make_payment_against_sales_return(
				setting, return_inv, flt(sum([flt(d["amount"]) for d in return_data["transactions"]]))
			)
		else:
			make_payment_against_sales_return(setting, return_inv, 0)

	if cint(setting.sync_delivery_return):
		restock_items_against_sales_return(setting, restocked_items, cstr(return_data["order_id"]))


def restock_items_against_sales_return(setting, restocked_items, order_id):
	# shopify doesn't pass the fulfillment ID against which return occured :/
	delivery_notes = frappe.db.get_all(
		"Delivery Note",
		filters={ORDER_ID_FIELD: order_id, "is_return": 0, "docstatus": 1},
		pluck="name",
	)

	for dn in delivery_notes:
		to_return = {}
		dn_items = frappe.db.get_all(
			"Delivery Note Item", filters={"parent": dn}, fields=["item_code", "qty"]
		)

		for item in dn_items:
			if not restocked_items.get(item.item_code):
				continue
			if restocked_items.get(item.item_code) <= item.qty:
				to_return[item.item_code] = restocked_items.pop(item.item_code)
			else:
				to_return[item.item_code] = item.qty
				restocked_items[item.item_code] -= item.qty

		if to_return:
			return_dn = make_return_document("Delivery Note", to_return, [], dn)
			return_dn.flags.ignore_mandatory = True
			return_dn.naming_series = (
				setting.return_delivery_series
				or setting.delivery_note_series
				or "DN-RET-Shopify-"
			)
			return_dn.insert().submit()
	
	if restocked_items:
		frappe.throw(_("Could not restock all items. Make sure delivery note has been created for all."))


def make_return_document(doctype, return_items, taxes, source_name: str, target_doc=None):
	def set_missing_values(source, target):
		target.is_return = 1
		target.return_against = source.name
		if taxes:
			target.taxes = []
			for tax in taxes:
				target.append("taxes", tax)
		else:
			for tax in target.get("taxes", []):
				if tax.charge_type == "Actual":
					tax.tax_amount = -1 * tax.tax_amount

	def update_item(source_doc, target_doc, source_parent):
		target_doc.qty = -1 * flt(return_items.get(target_doc.item_code, 1))
		target_doc.stock_qty = flt(target_doc.qty * target_doc.conversion_factor)
		if doctype == "Sales Invoice":
			target_doc.so_detail = source_doc.so_detail
			target_doc.sales_order = source_doc.sales_order
			target_doc.dn_detail = source_doc.dn_detail
			target_doc.delivery_note = source_doc.delivery_note
			target_doc.expense_account = source_doc.expense_account
			target_doc.sales_invoice_item = source_doc.name
		elif doctype == "Delivery Note":
			target_doc.against_sales_order = source_doc.against_sales_order
			target_doc.against_sales_invoice = source_doc.against_sales_invoice
			target_doc.so_detail = source_doc.so_detail
			target_doc.si_detail = source_doc.si_detail
			target_doc.expense_account = source_doc.expense_account
			target_doc.dn_detail = source_doc.name

	doclist = get_mapped_doc(
		doctype,
		source_name,
		{
			doctype: {"doctype": doctype, "validation": {"docstatus": ["=", 1],},},
			f"{doctype} Item": {
				"doctype": f"{doctype} Item",
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
	return_items, restocked_items = {}, {}
	refund_line_items = shopify_order.get("refund_line_items")

	for d in refund_line_items:
		line_item = d.get("line_item")
		if not line_item:
			continue

		item_code = get_item_code(line_item)

		return_items[item_code] = d.get("quantity", 1)
		if d.get("restock_type") == "return":
			restocked_items[item_code] = d.get("quantity", 1)

		for tax in line_item.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax),
					"description": (
						f"{get_tax_account_description(tax) or tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": -(flt(tax.get("price"))),
					"included_in_print_rate": 0,
					"cost_center": cost_center,
					"item_wise_tax_detail": json.dumps(
						{item_code: [flt(tax.get("rate")) * 100, -(flt(tax.get("price")))]}
					),
					"dont_recompute_tax": 1,
				}
			)

	return return_items, restocked_items, taxes


def make_payment_against_sales_return(setting, doc, paid_amount):
	write_off_account = frappe.get_cached_value("Company", doc.company, "write_off_account")
	if not paid_amount:
		"""
		Pass JV to write off outstanding if refunded amount is zero.
		ERPNext doesn't allow PE with zero paid amount
		"""
		journal_entry = frappe.new_doc("Journal Entry")
		journal_entry.company = doc.company
		journal_entry.posting_date = doc.posting_date or nowdate()
		journal_entry.reference_no = doc.name
		journal_entry.reference_date = doc.posting_date or nowdate()
		journal_entry.append(
			"accounts",
			{
				"account": doc.debit_to,
				"party_type": "Customer",
				"party": doc.customer,
				"debit_in_account_currency": abs(doc.rounded_total or doc.grand_total),
				"cost_center": setting.cost_center,
				"reference_type": doc.doctype,
				"reference_name": doc.return_against,
			},
		)
		journal_entry.append(
			"accounts",
			{
				"account": write_off_account,
				"credit_in_account_currency": abs(doc.rounded_total or doc.grand_total),
				"cost_center": setting.cost_center,
			},
		)
		journal_entry.flags.ignore_mandatory = True
		journal_entry.insert().submit()
		return

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(
		"Sales Invoice", doc.return_against, bank_account=setting.cash_bank_account
	)
	payment_entry.paid_amount = paid_amount
	payment_entry.set_gain_or_loss(
		account_details={
			"account": write_off_account,
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
