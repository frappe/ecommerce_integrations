# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, date_diff, get_datetime, today

from ecommerce_integrations.zenoti.doctype.zenoti_settings.zenoti_settings import sync_invoices
from ecommerce_integrations.zenoti.sales_transactions import (
	create_customer,
	prepare_customer_details,
)
from ecommerce_integrations.zenoti.utils import api_url, create_item, make_api_call

emp_gender_map = {
	-1: "NotSpecified",
	0: "Any",
	1: "Male",
	2: "Female",
	3: "ThirdGender",
	4: "Multiple",
}


class ZenotiCenter(Document):
	def sync_employees(self):
		employees = []
		for page in range(1, 100):
			url = api_url + "/centers/" + self.name + "/employees?size=100&page=" + str(page)
			all_emps = make_api_call(url)
			if all_emps.get("employees"):
				employees = employees + all_emps.get("employees")
			else:
				break

		for employee in employees:
			if not frappe.db.exists(
				"Employee",
				{
					"zenoti_employee_code": employee["code"],
					"employee_name": employee["personal_info"]["name"],
				},
			):
				self.create_emp(employee)

	def sync_customers(self):
		url = api_url + "guests?center_id=" + str(self.name)
		customers = make_api_call(url)
		if customers:
			total_page = customers["page_Info"]["total"] // 100
			for page in range(1, total_page + 2):
				url_ = url + "&size=100&page=" + str(page)
				all_customers = make_api_call(url_)
				if all_customers:
					for customer in all_customers["guests"]:
						if not frappe.db.exists("Customer", {"zenoti_guest_id": customer["id"]}):
							customer_details = prepare_customer_details(customer)
							create_customer(customer_details)
					frappe.db.commit()

	def sync_items(self):
		item_types = ["services", "products", "packages"]
		# item_types = ["memberships"]
		for item_type in item_types:
			url = api_url + "centers/" + str(self.name) + "/" + item_type
			products = make_api_call(url)
			if products:
				total_page = products["page_info"]["total"] // 100
				for page in range(1, total_page + 2):
					url_ = url + "?size=100&page=" + str(page)
					all_products = make_api_call(url_)
					if all_products:
						for product in all_products[item_type]:
							if not frappe.db.exists(
								"Item", {"zenoti_item_code": product["code"], "item_name": product["name"]}
							):
								create_item({}, product, item_type, self.name)
						frappe.db.commit()

	def sync_category(self):
		url = api_url + "centers/" + str(self.name) + "/categories?include_sub_categories=true"
		categories = make_api_call(url)
		if categories:
			total_page = categories["page_info"]["total"] // 100
			for page in range(1, total_page + 2):
				url_ = url + "&size=100&page=" + str(page)
				all_categories = make_api_call(url_)
				if all_categories:
					for category in all_categories["categories"]:
						if not frappe.db.exists("Zenoti Category", {"category_id": category["id"]}):
							self.make_category(category)

	def create_emp(self, emp):
		doc = frappe.new_doc("Employee")
		doc.zenoti_employee_id = emp["id"]
		doc.zenoti_center = self.name
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

	def make_category(self, category):
		frappe.get_doc(
			{
				"doctype": "Zenoti Category",
				"category_id": category["id"],
				"category_name": category["name"],
				"code": category["code"],
				"zenoti_center": self.name,
			}
		).insert(ignore_permissions=True)


def sync_employees_(center_id):
	center = frappe.get_doc("Zenoti Center", center_id)
	center.sync_employees()


def sync_customers_(center_id):
	center = frappe.get_doc("Zenoti Center", center_id)
	center.sync_customers()


def sync_items_(center_id):
	center = frappe.get_doc("Zenoti Center", center_id)
	center.sync_items()


def sync_category_(center_id):
	center = frappe.get_doc("Zenoti Center", center_id)
	center.sync_category()


@frappe.whitelist()
def sync(center, record_type, start_date=None, end_date=None):
	if record_type == "Sales Invoice":
		if get_datetime(end_date) < get_datetime(start_date):
			frappe.throw(_("To Date must be greater than From Date"))
		if date_diff(end_date, start_date) > 7:
			frappe.throw(_("Difference between From Date and To Date cannot be more than 7."))
		frappe.enqueue(
			"ecommerce_integrations.zenoti.doctype.zenoti_settings.zenoti_settings.sync_invoices",
			center_id=center,
			start_date=start_date,
			end_date=end_date,
			timeout=10000,
		)
	elif record_type == "Employees":
		frappe.enqueue(
			"ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync_employees_",
			center_id=center,
			timeout=10000,
		)
	elif record_type == "Customers":
		frappe.enqueue(
			"ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync_customers_",
			center_id=center,
			timeout=10000,
		)
	elif record_type == "Items":
		frappe.enqueue(
			"ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync_items_",
			center_id=center,
			timeout=10000,
		)
	elif record_type == "Categories":
		frappe.enqueue(
			"ecommerce_integrations.zenoti.doctype.zenoti_center.zenoti_center.sync_category_",
			center_id=center,
			timeout=10000,
		)
	elif record_type == "Stock Reconciliation":
		frappe.enqueue(
			"ecommerce_integrations.zenoti.doctype.zenoti_settings.zenoti_settings.sync_stocks",
			center=center,
			date=start_date,
			timeout=10000,
		)
