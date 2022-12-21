# Copyright (c) 2022, Frappe and contributors
# For license information, please see LICENSE

import frappe
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
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
		return_items = {}
		restocked_items = {}
		for refund_line_items in return_data["refund_line_items"]:
			erpnext_item = get_item_code(refund_line_items["line_item"])
			return_items[erpnext_item] = refund_line_items["quantity"]
			if refund_line_items["restock_type"] == "restock":
				restocked_items[erpnext_item] = refund_line_items["quantity"]

		# frappe.log_error(str(return_items))

		return_inv = make_return_inv(return_items, sales_invoice)
		return_inv.insert().submit()
		# new_item_list = []
		# sales_return = make_return_doc("Sales Invoice", sales_invoice)
		# for row in sales_return.items:
		# 	if not return_items.get(row.item_code):
		# 		continue
		# 	row.qty = -(return_items[row.item_code])
		# 	new_item_list.append(row)

		# sales_return.items = []
		# for idx, new_item in enumerate(new_item_list, start=1):
		# 	new_item.idx = idx
		# 	sales_return.append("items", new_item)

		# sales_return.insert().submit()
		create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def make_return_inv(return_items, source_name: str, target_doc=None):
	from frappe.model.mapper import get_mapped_doc

	def set_missing_values(source, target):
		doc = frappe.get_doc(target)
		doc.is_return = 1
		doc.return_against = source.name

		for tax in doc.get("taxes", []):
			if tax.charge_type == "Actual":
				tax.tax_amount = -1 * tax.tax_amount

		for d in doc.get("packed_items", []):
			d.qty = d.qty * -1

		if doc.get("discount_amount"):
			doc.discount_amount = -1 * source.discount_amount

		doc.run_method("calculate_taxes_and_totals")

	def update_item(source_doc, target_doc, source_parent):
		target_doc.qty = -1 * flt(return_items.get(target_doc.item_code, 1))
		target_doc.stock_qty = flt(target_doc.qty * target_doc.conversion_factor)

		target_doc.so_detail = source_doc.so_detail
		target_doc.sales_order = source_doc.sales_order

		target_doc.dn_detail = source_doc.dn_detail
		target_doc.delivery_note = source_doc.delivery_note

		target_doc.expense_account = source_doc.expense_account
		target_doc.sales_invoice_item = source_doc.name

	def update_terms(source_doc, target_doc, source_parent):
		target_doc.payment_amount = -source_doc.payment_amount

	doclist = get_mapped_doc(
		"Sales Invoice",
		source_name,
		{
			"Sales Invoice": {"doctype": "Sales Invoice", "validation": {"docstatus": ["=", 1],},},
			"Sales Invoice Item": {
				"doctype": "Sales Invoice Item",
				"field_map": {"serial_no": "serial_no", "batch_no": "batch_no", "bom": "bom"},
				"condition": lambda doc: doc.item_code in return_items.keys(),
				"postprocess": update_item,
			},
			"Payment Schedule": {"doctype": "Payment Schedule", "postprocess": update_terms},
		},
		target_doc,
		set_missing_values,
	)

	doclist.set_onload("ignore_price_list", True)

	return doclist
