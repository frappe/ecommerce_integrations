import frappe
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_stock_balance_for
from frappe import _
from frappe.utils import flt, now

from ecommerce_integrations.zenoti.utils import (
	api_url,
	check_for_item,
	get_center_code,
	get_cost_center,
	get_warehouse,
	make_api_call,
)


def process_stock_reconciliation(list_of_centers, error_logs):
	for center in list_of_centers:
		list_for_entry = []
		stock_quantities_of_products_in_a_center = retrieve_stock_quantities_of_products(center)
		if stock_quantities_of_products_in_a_center:
			center_code, center_code_err = get_center_code(center)
			if center_code_err:
				error_logs.append(center_code_err)
			make_list_for_entry(stock_quantities_of_products_in_a_center, list_for_entry, error_logs)
			if list_for_entry and center_code:
				cost_center, err_msg = get_cost_center(center_code)
				if err_msg:
					error_logs.append(err_msg)
				item_err_msg_list = check_for_item(list_for_entry, item_group="Products")
				if len(item_err_msg_list):
					item_err_msg = "\n".join(err for err in item_err_msg_list)
					error_logs.append(item_err_msg)
				if err_msg or len(item_err_msg_list):
					continue
				make_stock_reconciliation(list_for_entry, cost_center)


def retrieve_stock_quantities_of_products(center):
	url = api_url + "inventory/stock?center_id={0}&inventory_date={1}".format(center, now())
	stock_quantities_of_products = make_api_call(url)
	return stock_quantities_of_products


def make_list_for_entry(data, list_for_entry, error_logs):
	for entry in data["list"]:
		if entry["total_quantity"] > 0:
			warehouse, err_msg = get_warehouse(entry["center_code"])
			if err_msg:
				error_logs.append(err_msg)
				continue
			record = {
				"item_code": entry["product_code"],
				"item_name": entry["product_name"],
				"warehouse": warehouse,
				"qty": entry["total_quantity"],
				"allow_zero_valuation_rate": 1,
			}
			list_for_entry.append(record)
	return list_for_entry


def make_stock_reconciliation(list_for_entry, cost_center):
	doc = frappe.new_doc("Stock Reconciliation")
	doc.purpose = "Stock Reconciliation"
	doc.posting_date = frappe.utils.nowdate()
	doc.posting_time = frappe.utils.nowtime()
	doc.cost_center = cost_center
	doc.set("items", [])
	doc.difference_amount = 0.0
	add_items_to_reconcile(doc, list_for_entry)
	items = list(filter(lambda d: changed(d, doc), doc.items))
	if items:
		doc.items = items
		doc.insert()


def add_items_to_reconcile(doc, list_for_entry):
	for item in list_for_entry:
		invoice_item = {}
		for key, value in item.items():
			invoice_item[key] = value
		doc.append("items", invoice_item)


def changed(item, doc):
	item_dict = get_stock_balance_for(
		item.item_code, item.warehouse, doc.posting_date, doc.posting_time, batch_no=item.batch_no
	)

	if (
		(item.qty is None or item.qty == item_dict.get("qty"))
		and (item.valuation_rate is None or item.valuation_rate == item_dict.get("rate"))
		and (not item.serial_no or (item.serial_no == item_dict.get("serial_nos")))
	):
		return False
	else:
		# set default as current rates
		if item.qty is None:
			item.qty = item_dict.get("qty")

		if item.valuation_rate is None:
			item.valuation_rate = item_dict.get("rate")

		if item_dict.get("serial_nos"):
			item.current_serial_no = item_dict.get("serial_nos")
			if doc.purpose == "Stock Reconciliation" and not item.serial_no:
				item.serial_no = item.current_serial_no

		item.current_qty = item_dict.get("qty")
		item.current_valuation_rate = item_dict.get("rate")
		doc.difference_amount += flt(item.qty, item.precision("qty")) * flt(
			item.valuation_rate or item_dict.get("rate"), item.precision("valuation_rate")
		) - flt(item_dict.get("qty"), item.precision("qty")) * flt(
			item_dict.get("rate"), item.precision("valuation_rate")
		)
		return True
