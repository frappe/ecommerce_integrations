import json
import math

import frappe
import requests
from erpnext.controllers.accounts_controller import add_taxes_from_tax_template
from frappe import _
from frappe.utils import flt

api_url = "https://api.zenoti.com/v1/"

item_type = {
	"Services": "services",
	"Products": "products",
	"Memberships": "memberships",
	"Packages": "packages",
	"Gift or Pre-paid Cards": "gift_card",
}


def make_api_call(url):
	headers = get_headers()
	response = requests.request("GET", url=url, headers=headers)
	if response.status_code != 200:
		content = json.loads(response._content.decode("utf-8"))
		frappe.get_doc(
			{
				"doctype": "Zenoti Error Logs",
				"title": content["Message"],
				"error_message": content["InternalMessage"],
				"request_url": url,
				"status_code": content["StatusCode"],
			}
		).insert(ignore_permissions=True)

		return

	response_details = convert_str_to_json(response.text)

	return response_details


def get_headers():
	headers = {}
	headers["Authorization"] = "apikey " + frappe.db.get_single_value("Zenoti Settings", "api_key")
	return headers


def convert_str_to_json(string):
	try:
		json_data = json.loads(string)
	except Exception:
		json_acceptable_string = string.replace("'", '"')
		json_data = json.loads(json_acceptable_string)
	return json_data


def check_for_item(list_of_items, item_group):
	err_list = []
	for item in list_of_items:
		if not frappe.db.exists("Item", item["item_code"]):
			item_to_search = {"code": item["item_code"], "name": item["item_name"]}
			err_msg = make_item(item_to_search, item_group)
			if err_msg:
				err_list.append(err_msg)
	return err_list


def make_item(item, item_group):
	item_details, center = get_item_details(item, item_group)
	if not item_details:
		err_msg = _("Details for Item {0} does not exist in Zenoti").format(frappe.bold(item["name"]))
		return err_msg
	create_item(item, item_details, item_group, center)


def create_item(item_dict, item_details, item_group, center):
	item = frappe.new_doc("Item")
	item.zenoti_item_id = item_details["id"]
	item.item_code = item_details["code"] if "code" in item_details else item_dict["code"]
	item.item_name = item_details["name"]
	item.item_group = item_group
	item.is_stock_item = 0
	item.include_item_in_manufacturing = 0
	if item_group == "Products":
		item.is_stock_item = 1
	item.zenoti_item_type = get_zenoti_item_type(item_details)
	item.stock_uom = "Nos"
	item.zenoti_item_category = get_zenoti_category(item_details.get("category_id"), center)
	item.zenoti_item_sub_category = get_zenoti_category(item_details.get("sub_category_id"), center)
	if item_details.get("image_paths"):
		item.image = item_details["image_paths"]
	item.insert()


def get_item_details(item_dict, item_group):
	item_found = False
	list_of_center = get_list_of_centers()
	for center in list_of_center:
		list_of_items_in_a_center = get_list_of_items_in_a_center(center, item_group)
		for item in list_of_items_in_a_center:
			if item_group == "Memberships":
				if item_dict["name"] == item["name"]:
					item_found = True
					break
			elif "code" in item and item_dict["code"] == item["code"]:
				item_found = True
				break
			else:
				continue
		if item_found:
			break
		else:
			continue
	if item_found:
		return item, center
	else:
		return None, None


def get_list_of_centers():
	list_of_all_centers = []
	url = api_url + "centers"
	all_center = make_api_call(url)
	if all_center:
		process_list_of_all_centers_response(all_center, list_of_all_centers)
	return list_of_all_centers


def process_list_of_all_centers_response(all_center, list_of_all_centers):
	for center in all_center.get("centers"):
		list_of_all_centers.append(center["id"])


def get_list_of_items_in_a_center(center, item_group):
	list_of_all_items_in_center = []
	url1 = api_url + "centers/" + center + "/" + item_type[item_group] + "?size=100"
	all_items_in_center = make_api_call(url1)
	if all_items_in_center:
		if item_group == "Memberships":
			for item in all_items_in_center[item_type[item_group]]:
				list_of_all_items_in_center.append(item)
		else:
			size = all_items_in_center["page_info"]["total"]
			if size <= 100:
				for item in all_items_in_center[item_type[item_group]]:
					list_of_all_items_in_center.append(item)
			else:
				page = math.ceil(size / 100)
				for i in range(page):
					pg = i + 1
					url = api_url + "centers/" + center + "/" + item_type[item_group] + "?size=100" + "page=" + pg
					pagewise_items_in_center = make_api_call(url)
					for item in pagewise_items_in_center[item_type[item_group]]:
						list_of_all_items_in_center.append(item)

	return list_of_all_items_in_center


def get_zenoti_item_type(item_details):
	zenoti_item_type = ""
	if item_details.get("preferences"):
		if item_details["preferences"]["consumable"]:
			if item_details["preferences"]["retail"]:
				zenoti_item_type = "Both"
			else:
				zenoti_item_type = "Consumable"
		elif item_details["preferences"]["retail"]:
			zenoti_item_type = "Retail"

	return zenoti_item_type


def get_zenoti_category(category_id, center):
	category = ""
	if category_id:
		url = api_url + "centers/" + center + "/categories/" + category_id
		category_data = make_api_call(url)
		if category_data:
			category = category_data["name"]
	return category


def add_items(doc, item_data):
	for item in item_data:
		invoice_item = {}
		for key, value in item.items():
			invoice_item[key] = value
		if invoice_item.get("item_tax_template"):
			invoice_item["item_tax_template"] = frappe.db.get_value(
				"Item Tax Template", filters={"title": invoice_item["item_tax_template"]}
			)
			invoice_item["item_tax_rate"] = get_item_tax_rate(invoice_item["item_tax_template"])
		doc.append("items", invoice_item)


def get_item_tax_rate(item_tax_template):
	tax_values = frappe.db.get_value(
		"Item Tax Template Detail", {"parent": item_tax_template}, ["tax_type", "tax_rate"]
	)
	tax_dict = {tax_values[0]: tax_values[1]}
	return json.dumps(tax_dict)


def add_taxes(doc):
	for item in doc.items:
		add_taxes_from_tax_template(item, doc, db_insert=False)


def add_payments(doc, payments):
	for key, value in payments.items():
		payment = {}
		if flt(value) != 0:
			payment["mode_of_payment"] = key
			payment["amount"] = value
			doc.append("payments", payment)


def make_address(details, ref_docname, doctype):
	address_errors = validate_details_for_address(details, doctype)
	if address_errors:
		return
	country_id = details["country_id"]
	state_id = details["state_id"]
	county_details = get_country(country_id)
	state_details = get_state(str(country_id), state_id)

	if not check_for_country(county_details):
		return

	create_address(details, county_details, state_details, doctype, ref_docname)


def validate_details_for_address(details, doctype):
	err = False
	if not details["address1"] or not details["city"]:
		err = True
	return err


def get_country(country_id):
	list_of_countries = get_list_of_countries()
	country = None
	if list_of_countries:
		for countries in list_of_countries["countries"]:
			if countries["id"] == country_id:
				country = countries
	return country


def get_list_of_countries():
	url = api_url + "countries"
	all_countries = make_api_call(url)
	return all_countries


def get_state(country_id, state_id):
	list_of_states_of_the_country = get_list_of_states_of_a_country(country_id)
	state = None
	if list_of_states_of_the_country:
		for states in list_of_states_of_the_country["states"]:
			if states["id"] == state_id:
				state == states
	return state


def get_list_of_states_of_a_country(country_id):
	url = api_url + "countries/" + country_id + "/states"
	all_states = make_api_call(url)
	return all_states


def check_for_country(county_details):
	if not county_details or not frappe.db.exists("Country", county_details["name"]):
		return False
	return True


def create_address(details, county_details, state_details, doctype, ref_docname):
	address = frappe.new_doc("Address")
	address.address_type = "Billing"
	address.address_line1 = details["address1"]
	address.address_line2 = details["address2"]
	address.city = details["city"]
	address.country = county_details["name"]
	address.state = state_details["name"] if state_details else ""
	address.pincode = details["zip_code"]
	address.email_id = details["email"]
	address.phone = details["phone"]
	address.set("links", [])
	link = {"link_doctype": doctype, "link_name": ref_docname}
	address.append("links", link)

	address.insert()


def check_for_item_tax_template(item_tax_template):
	err_msg = ""
	if item_tax_template and not frappe.db.exists("Item Tax Template", {"title": item_tax_template}):
		err_msg = _("Item Tax Template {} does not exist.").format(frappe.bold(item_tax_template))
	return err_msg


def get_center_code(center_id):
	center = None
	err = ""
	url = api_url + "Centers/" + center_id
	center_details = make_api_call(url)
	if center_details and len(center_details.get("center")):
		center = center_details["center"]["code"]
		if not center:
			err = _("Could not find Center Id {} in Zenoti.").format(center_id)
	return center, err


def get_cost_center(center_code):
	err_msg = ""
	cost_center = frappe.db.get_value(
		"Zenoti Cost Center and Warehouse Mapping",
		{"zenoti_centre": center_code},
		["erpnext_cost_center"],
	)
	if not cost_center:
		err_msg = _("Center {0} is not linked to any ERPNext Cost Center in Zenoti Settings").format(
			frappe.bold(center_code)
		)
	return cost_center, err_msg


def get_warehouse(center_code):
	err_msg = ""
	warehouse = frappe.db.get_value(
		"Zenoti Cost Center and Warehouse Mapping", {"zenoti_centre": center_code}, ["erpnext_warehouse"]
	)
	if not warehouse:
		err_msg = _("Center {0} is not linked to any ERPNext Warehouse in Zenoti Settings").format(
			frappe.bold(center_code)
		)
	return warehouse, err_msg
