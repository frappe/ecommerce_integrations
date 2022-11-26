# Copyright (c) 2022, Frappe and contributors
# For license information, please see LICENSE

import frappe
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from frappe.utils import cint, cstr, getdate, nowdate

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
		"Sales Invoice", filters={ORDER_ID_FIELD: cstr(return_data["order_id"])}
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

		new_item_list = []
		sales_return = make_return_doc("Sales Invoice", sales_invoice)
		for row in sales_return.items:
			if not return_items.get(row.item_code):
				continue
			row.qty = -(return_items[row.item_code])
			new_item_list.append(row)

		sales_return.items = []
		for idx, new_item in enumerate(new_item_list, start=1):
			new_item.idx = idx
			sales_return.append("items", new_item)

		sales_return.insert().submit()
		create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)
