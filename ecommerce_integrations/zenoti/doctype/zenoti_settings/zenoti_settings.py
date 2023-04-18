# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import frappe
import requests
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, cstr, date_diff, get_datetime

from ecommerce_integrations.zenoti.purchase_transactions import process_purchase_orders
from ecommerce_integrations.zenoti.sales_transactions import process_sales_invoices
from ecommerce_integrations.zenoti.stock_reconciliation import process_stock_reconciliation
from ecommerce_integrations.zenoti.utils import api_url, get_all_centers, get_list_of_centers


class ZenotiSettings(Document):
	def validate(self):
		if not self.enable_zenoti:
			return

		url = api_url + "centers"
		headers = {}
		headers["Authorization"] = "apikey " + cstr(self.api_key)
		response = requests.request("GET", url=url, headers=headers)
		if response.status_code != 200:
			frappe.throw("Please verify the API Key")
		check_for_opening_stock_reconciliation()
		check_perpetual_inventory_disabled()
		setup_custom_fields()
		add_genders()
		make_item_group()
		make_item_tips()
		self.add_gift_and_prepaid_card_as_payment_mode()

	def add_gift_and_prepaid_card_as_payment_mode(self):
		payment_mode = "Gift and Prepaid Card"
		account = self.liability_income_account_for_gift_and_prepaid_cards
		add_mode_of_payments(payment_mode, account, self.company)


def add_mode_of_payments(payment_mode, account, company):
	if not frappe.db.get_value("Mode of Payment", payment_mode):
		doc = frappe.new_doc("Mode of Payment")
		doc.mode_of_payment = payment_mode
		doc.enabled = 1
		doc.type = "General"
		doc.set("accounts", [])
		add_payment_mode_accounts(doc, account, company)
		doc.insert()


def add_payment_mode_accounts(doc, account, company):
	account = account
	payment_mode_accounts = {"company": company, "default_account": account}
	doc.append("accounts", payment_mode_accounts)


def check_for_opening_stock_reconciliation():
	if not frappe.db.exists("Stock Reconciliation", {"purpose": "Opening Stock"}):
		frappe.throw(
			_(
				'Please reconcile the stocks using Stock Reconciliation with purpose "Opening'
				' Stock" before configuring this'
			)
		)


def sync_invoices(center_id=None, start_date=None, end_date=None):
	if cint(frappe.db.get_single_value("Zenoti Settings", "enable_zenoti")):
		if center_id or cint(frappe.db.get_single_value("Zenoti Settings", "enable_auto_syncing")):
			check_perpetual_inventory_disabled()
			interval = frappe.db.get_single_value("Zenoti Settings", "sync_interval")
			list_of_centers = [center_id] if center_id else get_list_of_centers()
			for row in list_of_centers:
				center = frappe.get_doc("Zenoti Center", row)
				last_sync = center.get("last_sync")
				if (
					last_sync and get_datetime() > get_datetime(add_to_date(last_sync, hours=cint(interval)))
				) or (start_date and end_date):
					error_logs = []
					process_sales_invoices(center, error_logs, start_date, end_date)
					if not last_sync or get_datetime(last_sync) < get_datetime(end_date):
						center.db_set("last_sync", get_datetime(end_date))
					frappe.db.commit()
					if len(error_logs):
						make_error_log(error_logs)


def sync_stocks(center=None, date=None):
	if cint(frappe.db.get_single_value("Zenoti Settings", "enable_zenoti")):
		if center or cint(frappe.db.get_single_value("Zenoti Settings", "enable_auto_syncing")):
			check_perpetual_inventory_disabled()
			error_logs = []
			list_of_centers = [center] if center else get_list_of_centers()
			for row in list_of_centers:
				center = frappe.get_doc("Zenoti Center", row)
				process_stock_reconciliation(center, error_logs, date)
				process_purchase_orders(center, error_logs, date)
				if len(error_logs):
					make_error_log(error_logs)


def check_perpetual_inventory_disabled():
	company = frappe.db.get_single_value("Zenoti Settings", "company")
	if frappe.db.get_value("Company", company, "enable_perpetual_inventory"):
		frappe.db.set_value("Company", company, "enable_perpetual_inventory", 0)


def make_error_log(error_logs):
	msg = "\n\n".join(err for err in error_logs)
	log = frappe.new_doc("Zenoti Error Logs")
	log.title = _("Errors occured at {}").format(get_datetime())
	log.error_message = msg
	log.insert()


def add_genders():
	for gender in ["NotSpecified", "Any", "ThirdGender", "Multiple"]:
		if not frappe.db.exists("Gender", gender):
			doc = frappe.new_doc("Gender")
			doc.gender = gender
			doc.insert()


def make_item_group():
	for item_group in ["Gift or Pre-paid Cards", "Memberships", "Packages"]:
		if not frappe.db.exists("Item Group", item_group):
			doc = frappe.new_doc("Item Group")
			doc.item_group_name = item_group
			doc.parent_item_group = "All Item Groups"
			doc.insert()


def make_item_tips():
	if not frappe.db.exists("Item", "Tips"):
		item = frappe.new_doc("Item")
		item.item_code = "Tips"
		item.item_name = "Tips"
		item.item_group = "All Item Groups"
		item.is_stock_item = 0
		item.include_item_in_manufacturing = 0
		item.stock_uom = "Nos"
		item.insert()


@frappe.whitelist()
def update_centers():
	list_of_centers = get_all_centers()
	for center in list_of_centers:
		if frappe.db.exists("Zenoti Center", center["id"]):
			center_doc = frappe.get_doc("Zenoti Center", center["id"])
			center_doc.code = center["code"]
			center_doc.center_name = center["name"]
			center_doc.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{
					"doctype": "Zenoti Center",
					"id": center["id"],
					"center_name": center["name"],
					"code": center["code"],
				}
			).insert(ignore_permissions=True)


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
			),
			dict(
				fieldname="zenoti_supplier_id",
				label="Zenoti Supplier ID",
				fieldtype="Data",
				insert_after="zenoti_supplier_code",
				read_only=1,
				print_hide=1,
				hidden=1,
			),
		],
		"Customer": [
			dict(
				fieldname="zenoti_guest_id",
				label="Zenoti Guest Id",
				fieldtype="Data",
				insert_after="salutation",
				read_only=1,
				print_hide=1,
				hidden=1,
			),
			dict(
				fieldname="zenoti_guest_code",
				label="Zenoti Guest Code",
				fieldtype="Data",
				insert_after="zenoti_guest_id",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_guest_code",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
		],
		"Item": [
			dict(
				fieldname="zenoti_item_code",
				label="Zenoti Item Code",
				fieldtype="Data",
				insert_after="item_code",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_item_id",
				label="Zenoti Item Id",
				fieldtype="Data",
				insert_after="zenoti_item_code",
				read_only=1,
				print_hide=1,
				hidden=1,
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
				options="\nRetail\nConsumable\nBoth",
				insert_after="zenoti_bussiness_unit_id",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_item_type",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname="zenoti_invoice_no",
				label="Zenoti Invoice No",
				fieldtype="Small Text",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_receipt_no",
				label="Zenoti Receipt No",
				fieldtype="Small Text",
				insert_after="zenoti_invoice_no",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_receipt_no",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
		],
		"Purchase Order": [
			dict(
				fieldname="zenoti_order_no",
				label="Zenoti Order No",
				fieldtype="Small Text",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_order_no",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
		],
		"Purchase Invoice": [
			dict(
				fieldname="zenoti_order_no",
				label="Zenoti Order No",
				fieldtype="Small Text",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_order_no",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
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
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_employee_username",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice Item": [
			dict(
				fieldname="zenoti_employee_details",
				label="Zenoti Employee Details",
				fieldtype="Section Break",
				insert_after="delivered_by_supplier",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="sold_by",
				label="Sold By",
				fieldtype="Link",
				options="Employee",
				insert_after="zenoti_employee_details",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="tips_column_break",
				label="",
				fieldtype="Column Break",
				options="Employee",
				insert_after="sold_by",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="tips",
				label="Tips",
				fieldtype="Data",
				insert_after="tips_column_break",
				read_only=1,
				print_hide=1,
			),
		],
		"Stock Entry": [
			dict(
				fieldname="zenoti_order_id",
				label="Zenoti Order Id",
				fieldtype="Small Text",
				insert_after="naming_series",
				read_only=1,
				print_hide=1,
				hidden=1,
			),
			dict(
				fieldname="zenoti_order_no",
				label="Zenoti Order No",
				fieldtype="Small Text",
				insert_after="zenoti_order_id",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname="zenoti_center",
				label="Zenoti Center",
				fieldtype="Link",
				insert_after="zenoti_order_no",
				options="Zenoti Center",
				read_only=1,
				print_hide=1,
			),
		],
	}

	create_custom_fields(custom_fields, update=False)
