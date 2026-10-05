import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, get_date_str, today

from ecommerce_integrations.zenoti.utils import (
	add_items,
	add_payments,
	add_taxes,
	api_url,
	check_for_item_tax_template,
	create_item,
	get_list_of_centers,
	make_address,
	make_api_call,
	make_item,
)

guest_gender_map = {0: "Female", 1: "Male", 3: "Other", -1: "NotSpecified"}

item_type = {
	0: "Services",
	2: "Products",
	3: "Memberships",
	4: "Packages",
	6: "Gift or Pre-paid Cards",
}

emp_gender_map = {
	-1: "NotSpecified",
	0: "Any",
	1: "Male",
	2: "Female",
	3: "ThirdGender",
	4: "Multiple",
}


def process_sales_invoices(center, error_logs, start_date=None, end_date=None):
	if not (start_date and end_date):
		start_date, end_date = get_start_end_date(center)
	list_of_invoice_for_center = get_list_of_invoices_for_center(center.name, start_date, end_date)
	for invoice in list_of_invoice_for_center:
		invoice_details = get_invoice_details(invoice, center, error_logs)
		if invoice_details:
			make_invoice(invoice_details)


def get_list_of_invoices_for_center(center, start_date, end_date):
	full_url = (
		api_url
		+ "sales/salesreport?center_id="
		+ center
		+ "&start_date="
		+ start_date
		+ "&end_date="
		+ end_date
		+ "&item_type=7&status=1"
	)
	sales_report = make_api_call(full_url)

	list_of_invoice_for_center = []
	invoice = []
	if sales_report:
		for report in sales_report["center_sales_report"]:
			if len(invoice) and invoice[0]["invoice_no"] != report["invoice_no"]:
				list_of_invoice_for_center.append(invoice)
				invoice = []

			if len(invoice) and invoice[0]["invoice_no"] == report["invoice_no"]:
				invoice.append(report)
			else:
				invoice.append(report)

		if len(invoice):
			list_of_invoice_for_center.append(invoice)

	return list_of_invoice_for_center


def get_start_end_date(center):
	if center.get("last_sync"):
		start_date = center.get("last_sync")
	else:
		start_date = today()
	end_date = today()

	return start_date, end_date


def get_invoice_details(invoice, center, error_logs):
	invoice_details = None
	if not frappe.db.exists("Sales Invoice", {"zenoti_invoice_no": invoice[0]["invoice_no"]}):
		data = validate_details(invoice, center, error_logs)
		customer = frappe.db.exists("Customer", {"zenoti_guest_id": invoice[0]["guest"]["guest_id"]})
		if data:
			date_time = invoice[0]["sold_on"].split("T")
			invoice_details = {
				"invoice_no": invoice[0]["invoice_no"],
				"receipt_no": invoice[0]["receipt_no"],
				"customer": customer,
				"posting_date": date_time[0],
				"posting_time": date_time[1],
				"cost_center": data["cost_center"],
				"set_warehouse": data["warehouse"],
				"item_data": data["item_data"],
				"total_qty": data["total_qty"],
				"is_return": data["is_return"],
				"payments": data["payments"],
				"rounding_adjustment": data["rounding_adjustment"],
			}
	return invoice_details


def validate_details(invoice, center, error_logs):
	data = {}
	err_msg = check_for_customer(invoice[0]["guest"]["guest_id"], invoice[0]["guest"]["guest_name"])
	if err_msg:
		make_error_log_msg(invoice, err_msg, error_logs)

	cost_center = center.get("erpnext_cost_center")
	if not cost_center:
		cost_center_err_msg = _("Center {0} is not linked to any ERPNext Cost Center.").format(
			frappe.bold(center.get("center_name"))
		)
		make_error_log_msg(invoice, cost_center_err_msg, error_logs)

	warehouse = center.get("erpnext_warehouse")
	if not warehouse:
		err_msg = _("Center {0} is not linked to any ERPNext Warehouse.").format(
			frappe.bold(center.get("center_name"))
		)
		make_error_log_msg(invoice, err_msg, error_logs)

	(
		item_data,
		total_qty,
		rounding_adjustment,
		payments,
		line_item_err_msg_list,
	) = process_sales_line_items(invoice, cost_center, center)
	if len(line_item_err_msg_list):
		line_item_err_msg = "\n".join(err for err in line_item_err_msg_list)
		make_error_log_msg(invoice, line_item_err_msg, error_logs)

	if not err_msg and not line_item_err_msg_list and item_data:
		data["cost_center"] = cost_center
		data["warehouse"] = warehouse
		data["item_data"] = item_data
		data["total_qty"] = total_qty
		data["is_return"] = 1 if flt(total_qty) < 0 else 0
		data["payments"] = payments
		data["rounding_adjustment"] = rounding_adjustment

	return data


def check_for_employee(emp_name, emp_code, center):
	err_msg = ""
	filters = {}
	if emp_name:
		filters["employee_name"] = emp_name
	if emp_code:
		filters["zenoti_employee_code"] = emp_code
	if not filters:
		err_msg = _("Details for Employee missing")
		return err_msg
	if not frappe.db.exists("Employee", filters):
		err_msg = center.sync_employees()
	return err_msg


def make_employee(emp_name, emp_code):
	list_of_centers = get_list_of_centers()
	err_msg = ""
	employee = None
	for center in list_of_centers:
		url = api_url + "/centers/" + center + "/employees"
		employee = get_emp(url, emp_name, emp_code, "employees")
		if not employee:
			url = api_url + "/centers/" + center + "/therapists?page=1&size=1000"
			employee = get_emp(url, emp_name, emp_code, "therapists")

		if employee:
			create_emp(employee)
			break

	if not employee:
		err_msg = _("Details for Employee {0} not found in Zenoti").format(frappe.bold(emp_name))
	return err_msg


def get_emp(employess, emp_name, emp_code, key):
	employee = None

	if employess:
		for emp in employess[key]:
			if emp["personal_info"]["name"] == emp_name and emp["code"] == emp_code:
				employee = emp
				break
	return employee


def filter_emp(url, emp_name, emp_code, key):
	employee = None
	all_emps = make_api_call(url)
	if all_emps:
		for emp in all_emps[key]:
			if emp["personal_info"]["name"] == emp_name and emp["code"] == emp_code:
				employee = emp
				break
	return employee


def create_emp(emp):
	doc = frappe.new_doc("Employee")
	doc.zenoti_employee_id = emp["id"]
	doc.zenoti_employee_code = emp["code"]
	doc.zenoti_employee_username = (
		emp["personal_info"]["user_name"] if "user_name" in emp["personal_info"] else ""
	)
	doc.first_name = emp["personal_info"]["first_name"]
	doc.last_name = emp["personal_info"]["last_name"]
	doc.employee_name = emp["personal_info"]["name"]
	doc.gender = emp_gender_map[emp["personal_info"]["gender"]]
	doc.date_of_joining = today()
	doc.date_of_birth = add_to_date(today(), years=-25)
	doc.insert()


def make_error_log_msg(invoice, err_msg, error_logs):
	invoice_no, receipt_no = "", ""
	if invoice[0]["invoice_no"]:
		invoice_no = _("Invoice No {0}").format(invoice[0]["invoice_no"])
	if invoice[0]["receipt_no"]:
		receipt_no = _("Reciept No {0}").format(invoice[0]["receipt_no"])
	msg = _("For {0} {1}.").format(frappe.bold(invoice_no), frappe.bold(receipt_no)) + "\n" + err_msg
	error_logs.append(msg)


def process_sales_line_items(invoice, cost_center, center):
	item_list = []
	err_msg_list = []
	total_qty = 0
	payments = {"Cash": 0, "Card": 0, "Custom": 0, "Points": 0, "Gift and Prepaid Card": 0}
	tip = 0
	rounding_adjustment = 0
	for line_item in invoice:
		add_to_list = False
		item_err_msg_list, item_group = check_for_items(line_item, center)
		if len(item_err_msg_list):
			item_err_msg = "\n".join(err for err in item_err_msg_list)
			err_msg_list.append(item_err_msg)
		emp_err_msg = check_for_employee(line_item["employee"]["name"], line_item["employee"]["code"], center)
		if emp_err_msg:
			err_msg_list.append(emp_err_msg)
		sold_by = frappe.db.get_value(
			"Employee",
			{
				"employee_name": line_item["employee"]["name"],
				"zenoti_employee_code": line_item["employee"]["code"],
			},
		)
		if not sold_by:
			msg = _("Employee {} not found in ERPNext.").format(frappe.bold(line_item["employee"]["name"]))
			frappe.log_error(msg)
		err_msg = check_for_item_tax_template(line_item["tax_code"])
		if err_msg:
			err_msg_list.append(err_msg)
			continue

		if len(err_msg_list) == 0:
			rate = abs(flt(line_item["sale_price"]) - flt(line_item["discount"])) / abs(
				flt(line_item["quantity"])
			)
			qty = line_item["quantity"] if line_item["sale_price"] >= 0 else line_item["quantity"] * -1
			tip += line_item["tips"]
			item = frappe.db.get_value("Item", {"zenoti_item_code": line_item["item"]["code"]}, "name")
			item_dict = {
				"item_code": item,
				"item_name": line_item["item"]["name"],
				"rate": rate,
				"discount_amount": line_item["discount"],
				"item_tax_template": line_item["tax_code"],
				"cost_center": cost_center,
				"qty": qty,
				"sold_by": sold_by,
			}
			if item_group == "Gift or Pre-paid Cards":
				item = frappe.db.get_value(
					"Item", {"zenoti_item_code": "Card No. " + line_item["item"]["code"]}, "name"
				)
				item_dict["income_account"] = frappe.db.get_single_value(
					"Zenoti Settings", "liability_income_account_for_gift_and_prepaid_cards"
				)
				item_dict["item_code"] = item
				item_dict["item_name"] = "Card No. " + item_dict["item_name"]

			total_qty += qty
			payments["Cash"] += line_item["cash"]
			payments["Card"] += line_item["card"]
			payments["Custom"] += line_item["custom"]
			payments["Points"] += line_item["points"]
			payments["Gift and Prepaid Card"] += flt(line_item["prepaid_card"]) + flt(
				line_item["prepaid_card_redemption"]
			)

			rounding_adjustment += line_item["rounding_adjustment"]

			for entry in ["cash", "card", "custom", "points", "prepaid_card", "prepaid_card_redemption"]:
				if line_item[entry] != 0:
					add_to_list = True
					break

			if add_to_list:
				item_list.append(item_dict)

	if tip != 0:
		tips_as_item = get_tips_as_item(tip, cost_center)
		item_list.append(tips_as_item)

		for key, value in payments.items():
			if value != 0:
				payments[key] += tip
				break

	return item_list, total_qty, rounding_adjustment, payments, err_msg_list


def get_tips_as_item(tip, cost_center):
	item_dict = {
		"item_code": "Tips",
		"item_name": "Tips",
		"rate": tip,
		"income_account": frappe.db.get_single_value(
			"Zenoti Settings", "liability_income_account_for_gift_and_prepaid_cards"
		),
		"cost_center": cost_center,
		"qty": 1,
	}
	return item_dict


def check_for_customer(guest_id, guest_name):
	err_msg = ""
	if not frappe.db.exists("Customer", {"zenoti_guest_id": guest_id}):
		err_msg = make_customer(guest_id, guest_name)
	return err_msg


def make_customer(guest_id, guest_name):
	guest_details = get_guest_details(guest_id)
	if not guest_details:
		err_msg = _("Details for Guest {} not found in Zenoti").format(frappe.bold(guest_name))
		return err_msg
	customer_details = prepare_customer_details(guest_details)
	create_customer(customer_details)


def create_customer(customer_details):
	customer = frappe.new_doc("Customer")
	customer.customer_name = customer_details["customer_name"]
	customer.zenoti_guest_id = customer_details["zenoti_guest_id"]
	customer.zenoti_guest_code = customer_details["zenoti_guest_code"]
	customer.zenoti_center = customer_details["zenoti_center"]
	if customer_details.get("gender"):
		customer.gender = customer_details["gender"]
	customer.customer_type = "Individual"
	customer.customer_group = (
		frappe.db.get_single_value("Zenoti Settings", "default_customer_group")
		if frappe.db.get_single_value("Zenoti Settings", "default_customer_group")
		else "All Customer Groups"
	)
	customer.territory = "All Territories"
	customer.insert()
	if customer_details.get("country_id"):
		make_address(customer_details, customer.name, "Customer")


def prepare_customer_details(guest_details):
	customer_details = {}
	customer_details["zenoti_guest_id"] = guest_details["id"]
	customer_details["zenoti_guest_code"] = guest_details["code"]
	customer_details["zenoti_center"] = guest_details["center_id"]
	customer_name = guest_details["personal_info"]["first_name"]
	if guest_details["personal_info"]["middle_name"]:
		customer_name += " " + guest_details["personal_info"]["middle_name"]
	customer_name += " " + guest_details["personal_info"]["last_name"]
	customer_details["customer_name"] = customer_name
	if guest_details["personal_info"]["gender"]:
		customer_details["gender"] = guest_gender_map[guest_details["personal_info"]["gender"]]
	if guest_details["address_info"]:
		customer_details["country_id"] = guest_details["address_info"]["country_id"]
		customer_details["state_id"] = guest_details["address_info"]["state_id"]
		customer_details["address1"] = guest_details["address_info"]["address1"]
		customer_details["address2"] = guest_details["address_info"]["address2"]
		customer_details["city"] = guest_details["address_info"]["city"]
		customer_details["zip_code"] = guest_details["address_info"]["zip_code"]

		customer_details["phone"] = guest_details["personal_info"]["mobile_phone"]["number"]
		customer_details["email"] = guest_details["personal_info"]["email"]

	return customer_details


def get_guest_details(guest_id):
	url = api_url + "guests/" + guest_id
	guest_details = make_api_call(url)
	return guest_details


def check_for_items(item, center):
	err_list = []
	group = cint(item["item"]["type"])
	if not frappe.db.exists(
		"Item", {"zenoti_item_code": item["item"]["code"], "item_group": item_type[group]}
	):
		if group == 6:
			err_msg = make_card_item(item)
		else:
			item_to_search = {"zenoti_item_code": item["item"]["code"], "name": item["item"]["name"]}
			err_msg = make_item(item_to_search, item_type[group], center.name)
		if err_msg:
			err_list.append(err_msg)
	return err_list, item_type[group]


def make_card_item(item_details):
	item = frappe.new_doc("Item")
	item.zenoti_item_code = "Card No. " + item_details["item"]["code"]
	item.item_name = "Card No. " + item_details["item"]["name"]
	item.item_group = "Gift or Pre-paid Cards"
	item.is_stock_item = 0
	item.include_item_in_manufacturing = 0
	item.stock_uom = "Nos"
	item.insert()


def make_invoice(invoice_details):
	doc = frappe.new_doc("Sales Invoice")
	doc.is_pos = 1
	doc.set_posting_time = 1
	doc.zenoti_invoice_no = invoice_details["invoice_no"]
	doc.zenoti_receipt_no = invoice_details["receipt_no"]
	doc.is_return = invoice_details["is_return"]
	doc.customer = invoice_details["customer"]
	doc.posting_date = invoice_details["posting_date"]
	doc.posting_time = invoice_details["posting_time"]
	doc.due_date = invoice_details["posting_date"]
	doc.cost_center = invoice_details["cost_center"]
	doc.selling_price_list = frappe.db.get_single_value("Zenoti Settings", "default_selling_price_list")
	doc.set_warehouse = invoice_details["set_warehouse"]
	doc.update_stock = 1
	doc.rounding_adjustment = invoice_details["rounding_adjustment"]
	doc.set("items", [])
	add_items(doc, invoice_details["item_data"])
	add_taxes(doc)
	doc.set("payments", [])
	add_payments(doc, invoice_details["payments"])
	doc.insert()
