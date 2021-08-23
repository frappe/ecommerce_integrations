import frappe

from frappe import _

from ecommerce_integrations.zenoti.utils import check_for_item, add_items, api_url, make_api_call, get_cost_center, get_warehouse

def process_transfer_orders(list_of_centers, error_logs):
	for center in list_of_centers:
		list_of_transfer_orders_for_center = get_list_of_transfer_orders_for_center(center)
		if list_of_transfer_orders_for_center and len(list_of_transfer_orders_for_center.get('orders')):
			for order in list_of_transfer_orders_for_center.get('orders'):
				process_transfer_order(order, error_logs)
        
def get_list_of_transfer_orders_for_center(center):
	start_date = frappe.utils.add_months(frappe.utils.today(), -1)
	end_date = frappe.utils.today()
	route = "inventory/transfer_orders?center_id="
	full_url = api_url + route + center + "&start_date=" + start_date + "&end_date=" + end_date
	all_orders = make_api_call(full_url)
	return all_orders

def process_transfer_order(order, error_logs):
	required_data = get_required_data_to_create_transfer_record(order, error_logs)
	for items in required_data:
		check_for_item(items['item_data'], "Product")
		create_transfer_record(items)

def get_required_data_to_create_transfer_record(order, error_logs):
	data = []
	if not frappe.db.exists("Stock Entry", {"zenoti_order_no": order['order_number'], "zenoti_order_id": order['order_id']}):
		center, err_msg = get_cost_center(order['center']['code'])
		if err_msg:
			msg = _("For Order no {}. ").format(order['order_number']) + err_msg
			error_logs.append(msg)
		to_warehouse, to_warehouse_err_msg = get_warehouse(order['center']['code'])
		if to_warehouse_err_msg:
			msg = _("For Order no {}. ").format(order['order_number']) + to_warehouse_err_msg
			error_logs.append(msg)
		from_warehouse, from_warehouse_err_msg = get_warehouse(order['vendor']['code'])
		if from_warehouse_err_msg:
			msg = _("For Order no {}. ").format(order['order_number']) + from_warehouse_err_msg
			error_logs.append(msg)
		item_data = process_transfer_partials(order.get('partials'), center)
		date_time = order['closed_date'].split("T")

		data_dict = {
			'from_warehouse' : from_warehouse,
			'to_warehouse' : to_warehouse,
			'date' :date_time[0],
			'time' : date_time[1],
			'order_no' : order['order_number'],
			'order_id' : order['order_id'],
			'status': order['status'],
			'item_data' : item_data,
			'cost_center' : center
		}
		if item_data[0]['is_return']:
			data_dict['from_warehouse'] = to_warehouse
			data_dict['to_warehouse'] = from_warehouse
		data.append(data_dict)

	return data

def process_transfer_partials(partials, center):
	item_list = []
	for item in partials[0]['line_items']:
		item_dict = {
			'item_code' : item['product_code'],
			'item_name' : item['product_name'],
			'basic_rate' : item['delivered_unit_price'],
			'cost_center': center
		}
		item_list.append(item_dict)
	
	for item in item_list:
		is_return = False
		qty = 0
		for partial in partials:
			for line_item in partial['line_items']:
				if item['item_code'] == line_item['product_code']:
					qty += line_item['delivered_retail_quantity']
					qty += line_item['delivered_consumable_quantity']
		if qty < 0:
			is_return = True

		item['qty'] = abs(qty)
		item['is_return'] = is_return
	return item_list

def create_transfer_record(order):
	doc = frappe.new_doc("Stock Entry")
	doc.stock_entry_type = "Material Transfer"
	doc.zenoti_order_id = order['order_id']
	doc.zenoti_order_no = order['order_no']
	doc.posting_date = order['date']
	doc.posting_time = order['time']
	doc.from_warehouse = order['from_warehouse']
	doc.to_warehouse = order['to_warehouse']
	doc.set('items', [])
	add_items(doc, order['item_data'])
	doc.insert()