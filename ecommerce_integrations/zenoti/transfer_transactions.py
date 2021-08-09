import frappe, erpnext

from ecommerce_integrations.zenoti.utils import check_for_item, add_items, api_url, make_api_call, check_for_cost_center, check_for_warehouses

def process_transfer_orders(list_of_centers):
	for center in list_of_centers:
		list_of_transfer_orders_for_center = get_list_of_transfer_orders_for_center(center)
		for order in list_of_transfer_orders_for_center.get('orders'):
			process_transfer_order(order, center)
        
def get_list_of_transfer_orders_for_center(center):
	start_date = frappe.utils.add_months(frappe.utils.today(), -1)
	end_date = frappe.utils.today()
	route = "inventory/transfer_orders?center_id="
	url_end = "&show_delivery_details=true&date_criteria=1&status=-1"
	full_url = api_url + route + center + "&start_date=" + start_date + "&end_date=" + end_date + url_end
	all_orders = make_api_call(full_url)
	return all_orders

def process_transfer_order(order, center):
	required_data = get_required_data_to_create_transfer_record(order)
	for items in required_data:
		if check_for_warehouses(items['from_warehouse'], items['to_warehouse']):
			check_for_item(items['item_data'], center, "Product")
			create_transfer_record(items)

def get_required_data_to_create_transfer_record(order):
	data = []
	center = order['center_name'] + ' - ' + frappe.get_cached_value('Company',  erpnext.get_default_company(),  "abbr")
	item_data = process_transfer_partials(order.get('partials'), center)
	date_time = order['ordered_date'].split("T")
	from_warehouse = order['vendor_name'] + ' - ' + frappe.get_cached_value('Company',  erpnext.get_default_company(),  "abbr")

	data_dict = {
		'from_warehouse' : from_warehouse,
		'to_warehouse' : center,
		'date' :date_time[0],
		'time' : date_time[1],
		'order_number' : order['order_number'],
		'status': order['status'],
		'item_data' : item_data,
		'is_return' : item_data[0]['is_return'],
		'cost_center' : center
	}
	data.append(data_dict)

	return data

def process_transfer_partials(partials, center):
	item_list = []
	for item in partials[0]['line_items']:
		item_dict = {
			'item_code' : item['product_code'],
			'item_name' : item['product_name'],
			'rate' : item['ordered_unit_price'],
			'cost_center': center
		}
		item_list.append(item_dict)
	
	for item in item_list:
		is_return = False
		qty = 0
		for partial in partials:
			for line_item in partial['line_items']:
				if item['item_code'] == line_item['product_code']:
					qty += line_item['ordered_retail_quantity']
					qty += line_item['ordered_consumable_quantity']
		if qty < 0:
			is_return = True

		item['qty'] = qty
		item['is_return'] = is_return
	return item_list

def create_transfer_record(order):
	if not check_for_cost_center(order['cost_center']):
		frappe.throw("Error")
	else:
		doc = frappe.new_doc("Material Request")
		doc.material_request_type = "Material Transfer"
		doc.transaction_date = order['date']
		doc.schedule_date = order['date']
		doc.set_from_warehouse = order['from_warehouse']
		doc.set_warehouse = order['to_warehouse']
		doc.set('items', [])
		add_items(doc, order)
		doc.submit()
		status, per_ordered = get_transfer_status(order)
		doc.db_set("status", status)
		doc.db_set("per_ordered", per_ordered)
		doc.reload()

def get_transfer_status(order):
	status = "Ordered"
	per_ordered = 0
	if order['status'] == "DELIVERED":
		status = "Received"
		per_ordered = 100

	return status, per_ordered