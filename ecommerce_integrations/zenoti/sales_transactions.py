import re
import frappe
from frappe.utils import cint

from frappe import _

from ecommerce_integrations.zenoti.utils import api_url, create_item, get_warehouse, make_api_call, make_address, make_item, add_items, add_taxes, check_for_item_tax_template, get_cost_center, add_payments, item_type
from frappe.utils.data import flt

guest_gender_map = {
	0: "Female",
	1: "Male",
	3: "Other",
	-1: "NotSpecified"
}

type = {
	0: "Services",
	2: "Products",
	3: "Memberships",
	4: "Packages",
	6: "Gift or Pre-paid Cards"
}

emp_gender_map = {
	-1: "NotSpecified" ,
	0: "Any",
	1: "Male", 
	2: "Female", 
	3: "ThirdGender",
	4: "Multiple"
}

def process_sales_invoices(list_of_centers, error_logs):
	for center in list_of_centers:
		list_of_invoice_for_center = get_list_of_invoices_for_center(center)
		for invoice in list_of_invoice_for_center:
			invoice_details = get_invoice_details(invoice, center, error_logs)
			if invoice_details:
				make_invoice(invoice_details)

def get_list_of_invoices_for_center(center):
	start_date = frappe.utils.add_days(frappe.utils.today(), -10)
	end_date = frappe.utils.add_days(frappe.utils.today(), -5)
	full_url = api_url + "sales/salesreport?center_id=" + center + "&start_date=" + start_date + "&end_date=" + end_date + '&item_type=7&status=1'
	sales_report = make_api_call(full_url)

	list_of_invoice_for_center = []
	invoice = []
	for report in sales_report['center_sales_report']:
		if len(invoice) and invoice[0]['invoice_no'] != report['invoice_no']:
			list_of_invoice_for_center.append(invoice)
			invoice = []

		if len(invoice) and invoice[0]['invoice_no'] == report['invoice_no']:
			invoice.append(report)
		else:
			invoice.append(report)
		
	return list_of_invoice_for_center

def get_invoice_details(invoice, center, error_logs):
	invoice_details = None
	if not frappe.db.exists("Sales Invoice", {"zenoti_invoice_no": invoice[0]['invoice_no']}):
		data = validate_details(invoice, center, error_logs)
		if data:
			date_time = invoice[0]['sold_on'].split("T")
			invoice_details = {
				'invoice_no': invoice[0]['invoice_no'],
				'receipt_no': invoice[0]['receipt_no'],
				'customer': invoice[0]['guest']['guest_name'],
				'posting_date': date_time[0],
				'posting_time':date_time[1],
				'cost_center': data['cost_center'],
				'set_warehouse': data['warehouse'],
				'item_data' : data['item_data'],
				'total_qty': data['total_qty'],
				'is_return':data['is_return']
				# 'employee': data['']
			}
	return invoice_details

def validate_details(invoice, center, error_logs):
	data = {}
	err_msg = check_for_customer(invoice[0]['guest']['guest_id'], invoice[0]['guest']['guest_name'])
	if err_msg:
		make_error_log_msg(invoice, err_msg, error_logs)

	item_err_msg_list = check_for_items(invoice, center)
	if len(item_err_msg_list):
		item_err_msg = "\n".join(err for err in item_err_msg_list)
		make_error_log_msg(invoice, item_err_msg, error_logs)

	cost_center, cost_center_err_msg = get_cost_center(invoice[0]['center']['center_name'])
	if cost_center_err_msg:
		make_error_log_msg(invoice, cost_center_err_msg, error_logs)
	warehouse, warehouse_err_msg = get_warehouse(invoice[0]['center']['center_name'])
	if warehouse_err_msg:
		make_error_log_msg(invoice, warehouse_err_msg, error_logs)
	item_data, total_qty, line_item_err_msg_list = process_sales_line_items(invoice, cost_center)
	if len(line_item_err_msg_list):
		line_item_err_msg = "\n".join(err for err in line_item_err_msg_list)
		make_error_log_msg(invoice, line_item_err_msg, error_logs)

	emp_err_msg = check_for_employee(invoice[0]['employee']['name'], invoice[0]['employee']['code'], center)
	if emp_err_msg:
		make_error_log_msg(invoice, emp_err_msg, error_logs)

	if not err_msg and not item_err_msg_list and not cost_center_err_msg and not line_item_err_msg_list:
		data['cost_center'] = cost_center
		data['warehouse'] = warehouse
		data['item_data'] = item_data
		data['total_qty'] = total_qty
		data['is_return'] = 1 if flt(total_qty) < 0 else 0
	return data

def check_for_employee(emp_name, emp_code, center):
	err_msg = ""
	if not frappe.db.exists("Employee", {"employee_name": emp_name, "zenoti_employee_code": emp_code}):
		err_msg = make_employee(emp_name, emp_code, center)
	return err_msg

def make_employee(emp_name, emp_code, center):
	err_msg = ""
	emp_found = False
	url = api_url + "/centers/" + center + "/employees?page=1&size=1000"
	all_emps = make_api_call(url)
	for emp in all_emps:
		if emp['personal_info']['name'] == emp_name and emp['code'] == emp_code:
			emp_found = True
			create_emp(emp)
	if not emp_found:
		err_msg = _("Details for Employee {} not found in Zenoti").format(emp_name)
	return err_msg

def create_emp(emp):
	doc = frappe.new_doc("Employee")
	doc.zenoti_employee_id = emp['id']
	doc.zenoti_employee_code = emp['code']
	doc.zenoti_employee_username = emp['personal_info']['user_name']
	doc.first_name = emp['personal_info']['first_name']
	doc.last_name = emp['personal_info']['last_name']
	doc.employee_name = emp['personal_info']['name']
	doc.gender = emp_gender_map[emp['personal_info']['gender']]
	doc.insert()
	return True
	
def make_error_log_msg(invoice, err_msg, error_logs):
	invoice_no, receipt_no = "", ""
	if invoice[0]['invoice_no']:
		invoice_no = _("Invoice No {0}").format(invoice[0]['invoice_no'])
	if invoice[0]['receipt_no']:
		receipt_no = _("Reciept No {0}").format(invoice[0]['receipt_no'])
	msg = _("For {0} {1}. ").format(invoice_no, receipt_no) + err_msg
	error_logs.append(msg)
	
def process_sales_line_items(invoice, cost_center):
	item_list = []
	err_msg_list = []
	total_qty = 0
	for line_item in invoice:
		err_msg = check_for_item_tax_template(line_item['tax_code'])
		if err_msg:
			err_msg_list.append(err_msg)
			continue

		if len(err_msg_list) == 0:
			item_dict = {
				'item_code' : line_item['item']['code'],
				'item_name' : line_item['item']['name'],
				'rate' : abs(flt(line_item['sale_price']) - flt(line_item['discount'])),
				'discount_amount': line_item['discount'],
				'item_tax_template' : line_item['tax_code'],
				'cost_center': cost_center,
				'qty': line_item['quantity'] if  flt(line_item['sale_price']) > 0 else flt(line_item['quantity']) * -1,
			}
			item_list.append(item_dict)
			total_qty += line_item['quantity'] if  flt(line_item['sale_price']) > 0 else flt(line_item['quantity']) * -1,

	return item_list, total_qty, err_msg_list


def check_for_customer(guest_id, guest_name):
	err_msg = ""
	if not frappe.db.exists("Customer", {"zenoti_guest_id": guest_id}):
		err_msg = create_customer(guest_id, guest_name)
	return err_msg

def create_customer(guest_id, guest_name):
	guest_details = get_guest_details(guest_id)
	if not guest_details:
		err_msg = _("Details for Guest {} not found in Zenoti").format(guest_name)
		return err_msg
	customer_details = prepare_customer_details(guest_details)
	
	customer = frappe.new_doc("Customer")
	customer.customer_name = customer_details['customer_name']
	customer.zenoti_guest_id = customer_details['zenoti_guest_id']
	customer.zenoti_guest_code = customer_details['zenoti_guest_code']
	if customer_details.get('gender'):
		customer.gender = customer_details['gender']
	customer.customer_type = "Individual"
	customer.customer_group = frappe.db.get_single_value("Zenoti Settings", "default_customer_group") if frappe.db.get_single_value("Zenoti Settings", "default_customer_group") else "All Customer Groups"
	customer.territory = "All Territories"
	customer.insert()
	if customer_details.get('country_id'):
		make_address(customer_details, customer.name, "Customer")

def prepare_customer_details(guest_details):
	customer_details = {}
	customer_details['zenoti_guest_id'] = guest_details['id']
	customer_details['zenoti_guest_code'] = guest_details['code']
	customer_name = guest_details['personal_info']['first_name']
	if guest_details['personal_info']['middle_name']:
		customer_name += ' ' + guest_details['personal_info']['middle_name']
	customer_name += ' ' + guest_details['personal_info']['last_name']
	customer_details['customer_name'] = customer_name
	if guest_details['personal_info']['gender']:
		customer_details['gender'] = guest_gender_map[guest_details['personal_info']['gender']]
	if guest_details['address_info']:
		customer_details['country_id'] = guest_details['address_info']['country_id']
		customer_details['state_id'] = guest_details['address_info']['state_id']
		customer_details['address1'] = guest_details['address_info']['address1']
		customer_details['address2'] = guest_details['address_info']['address2']
		customer_details['city'] = guest_details['address_info']['city']
		customer_details['zip_code'] = guest_details['address_info']['zip_code']

		customer_details['phone'] = guest_details['personal_info']['mobile_phone']['number']
		customer_details['email'] = guest_details['personal_info']['email']

	return customer_details

def get_guest_details(guest_id):
	url = api_url + "guests/" + guest_id
	guest_details = make_api_call(url)
	return guest_details

def check_for_items(invoice_items, center):
	err_list = []
	for item in invoice_items:
		group = cint(item['item']['type'])
		if not frappe.db.exists("Item", {"item_code": item['item']['code'], "item_group": type[group]}):	
			if group == 6:
				err_msg = make_card_item(item)
			else:
				err_msg = make_item(item['item']['code'], center, type[group])
			if err_msg:
				err_list.append(err_msg)
	return err_list

def make_card_item(item_details):
	item = frappe.new_doc("Item")
	item.item_code = item_details['item']['code']
	item.item_name = item_details['item']['name']
	item.item_group = "Gift or Pre-paid Cards"
	item.is_stock_item = 0
	item.include_item_in_manufacturing = 0
	item.stock_uom = "Nos"
	item.insert()

def make_invoice(invoice_details):
	doc = frappe.new_doc("Sales Invoice")
	doc.is_pos = 1
	doc.zenoti_invoice_no = invoice_details['invoice_no']
	doc.zenoti_receipt_no = invoice_details['receipt_no']
	doc.is_return = invoice_details['is_return']
	doc.customer = invoice_details['customer']
	doc.posting_date = invoice_details['posting_date']
	doc.posting_time = invoice_details['posting_time']
	doc.due_date = invoice_details['posting_date']
	doc.cost_center = invoice_details['cost_center']
	doc.selling_price_list = frappe.db.get_single_value("Zenoti Settings", "default_selling_price_list")
	doc.set_warehouse = invoice_details['set_warehouse']
	doc.update_stock = 1
	doc.set('items', [])
	add_items(doc, invoice_details['item_data'])
	add_taxes(doc)
	add_payments(doc)
	doc.insert()