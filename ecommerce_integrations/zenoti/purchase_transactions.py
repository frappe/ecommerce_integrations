import frappe
from frappe import _
from frappe.utils import add_to_date

from ecommerce_integrations.zenoti.utils import (
	add_taxes,
	api_url,
	check_for_item,
	check_for_item_tax_template,
	get_item_tax_rate,
	make_address,
	make_api_call,
)


def process_purchase_orders(center, error_logs, date=None):
	list_of_purchase_orders_for_center = get_list_of_purchase_orders_for_center(center.name, date)
	if list_of_purchase_orders_for_center and len(list_of_purchase_orders_for_center.get("orders")):
		for order in list_of_purchase_orders_for_center.get("orders"):
			process_purchase_order(order, center, error_logs)


def get_list_of_purchase_orders_for_center(center, date=None):
	if date:
		start_date = end_date = date
	else:
		end_date = frappe.utils.nowdate()
		start_date = add_to_date(end_date, days=-1)
	route = "inventory/purchase_orders?center_id="
	url_end = "&show_delivery_details=true&date_criteria=1&status=-1"
	full_url = api_url + route + center + "&start_date=" + start_date + "&end_date=" + end_date + url_end
	all_orders = make_api_call(full_url)
	return all_orders


def process_purchase_order(order, center, error_logs):
	required_data = get_required_data_to_create_purchase_record(order, center, error_logs)
	if required_data:
		for items in required_data:
			supplier_err_msg = check_for_supplier(items["supplier"])
			if supplier_err_msg:
				msg = _("For Order no {}.").format(items["order_number"]) + " " + supplier_err_msg
				error_logs.append(msg)

			item_err_msg_list = check_for_item(items["item_data"], item_group="Products")
			if len(item_err_msg_list):
				item_err_msg = "\n".join(err for err in item_err_msg_list)
				msg = _("For Order no {}.").format(items["order_number"]) + "\n" + item_err_msg
				error_logs.append(msg)

			if not supplier_err_msg and not len(item_err_msg_list):
				create_purchase_record(items)


def check_for_supplier(supplier_code):
	err_msg = ""
	if not supplier_code:
		err_msg = _("Vendor Code is empty")
		return err_msg
	elif not frappe.db.exists("Supplier", {"zenoti_supplier_code": supplier_code}):
		sync_supplier()


def sync_supplier():
	url = api_url + "vendors"
	suppliers = make_api_call(url)
	if suppliers:
		total_page = suppliers["page_info"]["total"] // 100
		for page in range(1, total_page + 2):
			url_ = url + "?size=100&page=" + str(page)
			all_suppliers = make_api_call(url_)
			if all_suppliers:
				for supplier in all_suppliers["vendors"]:
					if not frappe.db.exists("Supplier", {"zenoti_supplier_code": supplier["code"]}):
						try:
							create_supplier(supplier)
						except Exception:
							frappe.log_error()


def create_supplier(supplier_details):
	supplier_details["phone"] = supplier_details["work_phone"]["number"]
	supplier = frappe.new_doc("Supplier")
	supplier.zenoti_supplier_code = supplier_details["code"]
	supplier.supplier_name = supplier_details["name"]
	supplier.supplier_group = (
		frappe.db.get_single_value("Zenoti Settings", "default_supplier_group")
		if frappe.db.get_single_value("Zenoti Settings", "default_supplier_group")
		else "All Supplier Groups"
	)
	supplier.supplier_details = supplier_details["description"]
	supplier.insert()
	make_address(supplier_details, supplier.name, "Supplier")


def get_required_data_to_create_purchase_record(order, center, error_logs):
	data = []
	if not frappe.db.exists(
		"Purchase Invoice", {"zenoti_order_no": order["order_number"]}
	) and not frappe.db.exists("Purchase Order", {"zenoti_order_no": order["order_number"]}):
		cost_center = center.get("erpnext_cost_center")
		if not cost_center:
			err_msg = _("Center {0} is not linked to any ERPNext Cost Center.").format(
				frappe.bold(center.get("center_name"))
			)
			msg = _("For Order no {}.").format(order["order_number"]) + " " + err_msg
			error_logs.append(msg)

		item_data, item_err_msg_list = process_purchase_partials(order.get("partials"), cost_center)
		if len(item_err_msg_list):
			item_err_msg = "\n".join(err for err in item_err_msg_list)
			msg = _("For Order no {}.").format(order["order_number"]) + "\n" + item_err_msg
			error_logs.append(msg)

		if not len(item_err_msg_list):
			date_time = order["ordered_date"].split("T")

			data_dict = {
				"supplier": order["vendor"]["code"],
				"date": date_time[0],
				"time": date_time[1],
				"order_number": order["order_number"],
				"status": order["status"],
				"item_data": item_data,
				"is_return": item_data[0]["is_return"],
				"cost_center": cost_center,
			}
			data.append(data_dict)

	return data


def process_purchase_partials(partials, center):
	item_list = []
	err_msg_list = []
	for item in partials[0]["line_items"]:
		err_msg = check_for_item_tax_template(item["ordered_tax_group_name"])
		if err_msg:
			err_msg_list.append(err_msg)
			continue

		if len(err_msg_list) == 0:
			item_dict = {
				"item_code": item["product_code"],
				"item_name": item["product_name"],
				"supplier_part_no": item["vendor_product_part_number"],
				"rate": item["ordered_unit_price"],
				"item_tax_template": item["ordered_tax_group_name"],
				"cost_center": center,
			}
			item_list.append(item_dict)

	if len(err_msg_list) == 0:
		for item in item_list:
			is_return = False
			qty = 0
			for partial in partials:
				for line_item in partial["line_items"]:
					if item["item_code"] == line_item["product_code"]:
						qty += line_item["ordered_retail_quantity"]
						qty += line_item["ordered_consumable_quantity"]
			if qty < 0:
				is_return = True
				del item["supplier_part_no"]

			item["qty"] = qty
			item["is_return"] = is_return
	return item_list, err_msg_list


def create_purchase_record(order):
	if order["is_return"]:
		doc = frappe.new_doc("Purchase Invoice")
		doc.is_return = 1
		doc.posting_date = order["date"]
		doc.posting_time = order["time"]
		doc.set_posting_time = 1
		doc.cost_center = order["cost_center"]
		doc.update_stock = 1
	else:
		doc = frappe.new_doc("Purchase Order")
		doc.transaction_date = order["date"]
		doc.schedule_date = order["date"]
	doc.zenoti_order_no = order["order_number"]
	supplier = frappe.db.exists("Supplier", {"zenoti_supplier_code": order["supplier"]})
	doc.supplier = supplier
	doc.buying_price_list = frappe.db.get_single_value("Zenoti Settings", "default_buying_price_list")
	doc.set_warehouse = frappe.db.get_single_value("Zenoti Settings", "default_purchase_warehouse")
	doc.set("items", [])
	add_items(doc, order["item_data"])
	add_taxes(doc)
	if order["is_return"]:
		doc.insert()
	else:
		doc.submit()
		status, per_received = get_order_status(order)
		doc.db_set("status", status)
		doc.db_set("per_received", per_received)
		doc.reload()


def add_items(doc, item_data):
	for item in item_data:
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

		if invoice_item.get("item_tax_template"):
			invoice_item["item_tax_template"] = frappe.db.get_value(
				"Item Tax Template", filters={"title": invoice_item["item_tax_template"]}
			)
			invoice_item["item_tax_rate"] = get_item_tax_rate(invoice_item["item_tax_template"])
		doc.append("items", invoice_item)


def get_order_status(order):
	status = "To Receive and Bill"
	per_received = 0
	if order["status"] == "DELIVERED":
		status = "To Bill"
		per_received = 100

	return status, per_received
