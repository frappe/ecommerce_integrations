import frappe
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_stock_balance_for
from frappe import _
from frappe.utils import flt, now

from ecommerce_integrations.zenoti.utils import api_url, check_for_item, make_api_call


def process_stock_reconciliation(center, error_logs, date=None):
	if not date:
		date = now()
	list_for_entry = []
	stock_quantities_of_products_in_a_center = retrieve_stock_quantities_of_products(center.name, date)
	if stock_quantities_of_products_in_a_center:
		cost_center = center.get("erpnext_cost_center")
		if not cost_center:
			err_msg = _("Center {0} is not linked to any ERPNext Cost Center.").format(
				frappe.bold(center.get("center_name"))
			)
			error_logs.append(err_msg)
		make_list_for_entry(center, stock_quantities_of_products_in_a_center, list_for_entry, error_logs)
		if list_for_entry and center.get("code"):
			item_err_msg_list = check_for_item(list_for_entry, "Products", center.name)
			if len(item_err_msg_list):
				item_err_msg = "\n".join(err for err in item_err_msg_list)
				error_logs.append(item_err_msg)
			if not (len(item_err_msg_list)):
				make_stock_reconciliation(list_for_entry, date, cost_center)


def retrieve_stock_quantities_of_products(center, date):
	url = api_url + f"inventory/stock?center_id={center}&inventory_date={date}"
	stock_quantities_of_products = make_api_call(url)
	return stock_quantities_of_products


def make_list_for_entry(center, data, list_for_entry, error_logs):
	for entry in data["list"]:
		if entry["total_quantity"] > 0:
			warehouse = center.get("erpnext_warehouse")
			if not warehouse:
				err_msg = _("Center {0} is not linked to any ERPNext Warehouse.").format(
					frappe.bold(center.get("center_name"))
				)
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


def make_stock_reconciliation(list_for_entry, date, cost_center):
	doc = frappe.new_doc("Stock Reconciliation")
	doc.purpose = "Stock Reconciliation"
	doc.set_posting_time = 1
	doc.posting_date = date
	doc.posting_time = "00:00:00"
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
			if key == "item_code":
				item_code = frappe.db.get_value(
					"Item",
					{"zenoti_item_code": item["item_code"], "item_name": item["item_name"]},
					"item_code",
				)
				invoice_item["item_code"] = item_code
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
