# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import get_datetime, add_to_date, cint

from erpnext import get_default_company

from ecommerce_integrations.zenoti.utils import get_list_of_centers, api_url
from ecommerce_integrations.zenoti.purchase_transactions import process_purchase_orders
from ecommerce_integrations.zenoti.sales_transactions import process_sales_invoices
from ecommerce_integrations.zenoti.stock_reconciliation import process_stock_reconciliation
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

import requests

class ZenotiSettings(Document):

	def validate(self):
		url = api_url + 'centers'
		headers = {}
		headers['Authorization'] = "apikey " + self.api_key
		response = requests.request("GET",url=url, headers=headers)
		if response.status_code != 200:
			frappe.throw("Please verify the API Key")	
		setup_custom_fields()
		make_item_group()
		self.add_gift_and_prepaid_card_as_payment_mode()

	def add_gift_and_prepaid_card_as_payment_mode(self):
		if not frappe.db.get_value("Mode of Payment", "Gift and Prepaid Card"):
			doc = frappe.new_doc("Mode of Payment")
			doc.mode_of_payment = "Gift and Prepaid Card"
			doc.enabled = 1
			doc.type = "General"
			doc.set("accounts", [])
			self.add_payment_mode_accounts(doc)
			doc.insert()

	def add_payment_mode_accounts(self, doc):
		company = get_default_company()
		account = self.default_liability_account
		payment_mode_accounts = {
			"company": company,
			"default_account": account
		}
		doc.append("accounts", payment_mode_accounts)


def sync_invoices():
	if frappe.db.get_single_value("Zenoti Settings", "enable_zenoti"):
		last_sync = frappe.db.get_single_value("Zenoti Settings", "last_sync")
		interval = frappe.db.get_single_value("Zenoti Settings", "sync_interval")
		if last_sync and get_datetime() > get_datetime(add_to_date(last_sync, hours=cint(interval))):
			error_logs = []
			list_of_centers = get_list_of_centers()
			if len(list_of_centers):
				process_sales_invoices(list_of_centers, error_logs)
				frappe.db.set_value("Zenoti Settings", "Zenoti Settings", "last_sync", get_datetime())
				if len(error_logs):
					make_error_log(error_logs)

def sync_stocks():
	if frappe.db.get_single_value("Zenoti Settings", "enable_zenoti"):
		error_logs = []
		list_of_centers = get_list_of_centers()
		if len(list_of_centers):
			process_purchase_orders(list_of_centers, error_logs)
			process_stock_reconciliation(list_of_centers, error_logs)
			if len(error_logs):
					make_error_log(error_logs)

def make_error_log(error_logs):
	msg = "\n".join(err for err in error_logs)
	log = frappe.new_doc("Zenoti Error Logs")
	log.title = _("Errors occured at {}").format(get_datetime())
	log.error_message = msg
	log.insert()

def make_item_group():
	if not frappe.db.get_value("Item Group", "Gift or Pre-paid Cards"):
		doc = frappe.new_doc("Item Group")
		doc.item_group_name = "Gift or Pre-paid Cards"
		doc.parent_item_group = "All Item Groups"
		doc.insert()

def setup_custom_fields():
	custom_fields = {
		"Supplier": [
			dict(
				fieldname="zenoti_supplier_code",
				label="Zenoti Supplier Code",
				fieldtype="Data",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			)
		],
		"Customer": [
			dict(
				fieldname="zenoti_guest_id",
				label="Zenoti Guest Id",
				fieldtype="Data",
				insert_after="salutation",
				read_only=1,
				print_hide=1,
				hidden=1
			),
			dict(
				fieldname="zenoti_guest_code",
				label="Zenoti Guest Code",
				fieldtype="Data",
				insert_after="zenoti_guest_id",
				read_only=1,
				print_hide=1
			),
		],
		"Item": [
			dict(
				fieldname="zenoti_item_id",
				label="Zenoti Item Id",
				fieldtype="Data",
				insert_after="item_code",
				read_only=1,
				print_hide=1,
				hidden=1
			),
			dict(
				fieldname="zenoti_item_category",
				label="Zenoti Item Category",
				fieldtype="Data",
				insert_after="item_group",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_item_sub_category",
				label="Zenoti Item Sub Category",
				fieldtype="Data",
				insert_after="zenoti_item_category",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_bussiness_unit_id",
				label="Zenoti Bussiness Unit Id",
				fieldtype="Data",
				insert_after="zenoti_item_category",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_item_type",
				label="Zenoti Item Type",
				fieldtype="Select",
				options='\nRetail\nConsumable\nBoth',
				insert_after="zenoti_bussiness_unit_id",
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname="zenoti_invoice_no",
				label="Zenoti Invoice No",
				fieldtype="Data",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_receipt_no",
				label="Zenoti Receipt No",
				fieldtype="Data",
				insert_after="zenoti_invoice_no",
				read_only=1,
				print_hide=1,
			)
		],
		"Purchase Order": [
			dict(
				fieldname="zenoti_order_no",
				label="Zenoti Order No",
				fieldtype="Data",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			)
		],
		"Purchase Invoice": [
			dict(
				fieldname="zenoti_order_no",
				label="Zenoti Order No",
				fieldtype="Data",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			)
		],
		"Employee": [
			dict(
				fieldname="zenoti_employee_id",
				label="Zenoti Employee Id",
				fieldtype="Data",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_employee_code",
				label="Zenoti Employee Code",
				fieldtype="Data",
				insert_after="zenoti_employee_id",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_employee_username",
				label="Zenoti Employee Username",
				fieldtype="Data",
				insert_after="zenoti_employee_code",
				read_only=1,
				print_hide=1,
			)
		]
	}

	create_custom_fields(custom_fields, update=False)
