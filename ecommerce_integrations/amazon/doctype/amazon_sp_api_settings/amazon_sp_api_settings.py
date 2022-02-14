# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document

from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
	get_orders,
	get_products_details,
)


class AmazonSPAPISettings(Document):
	def validate(self):
		if self.enable_amazon == 1:
			setup_custom_fields()
		else:
			self.enable_sync = 0
		if self.max_retry_limit and self.max_retry_limit > 5:
			frappe.throw(frappe._("Value for <b>Max Retry Limit</b> must be less than or equal to 5."))

	def after_save(self):
		if not self.is_old_data_migrated:
			migrate_old_data()
			self.db_set("is_old_data_migrated", 1)

	@frappe.whitelist()
	def get_products_details(self):
		if self.enable_amazon == 1:
			get_products_details()

	@frappe.whitelist()
	def get_order_details(self):
		if self.enable_amazon == 1:
			get_orders(created_after=self.after_date)


# Called via a hook in every hour.
def schedule_get_order_details():
	amz_settings = frappe.get_doc("Amazon SP API Settings")
	if amz_settings.enable_amazon and amz_settings.enable_sync:
		get_orders(created_after=amz_settings.after_date)


def setup_custom_fields():
	custom_fields = {
		"Sales Order": [
			dict(
				fieldname="amazon_order_id",
				label="Amazon Order ID",
				fieldtype="Data",
				insert_after="title",
				read_only=1,
				print_hide=1,
			)
		],
	}

	create_custom_fields(custom_fields)


def migrate_old_data():
	column_exists = frappe.db.has_column("Item", "amazon_item_code")

	if column_exists:
		item = frappe.qb.DocType("Item")
		items = (frappe.qb.from_(item).select("*").where(item.amazon_item_code.notnull())).run(
			as_dict=True
		)

		for item in items:
			if not frappe.db.exists("Ecommerce Item", {"erpnext_item_code": item.name}):
				ecomm_item = frappe.new_doc("Ecommerce Item")
				ecomm_item.integration = "Amazon"
				ecomm_item.erpnext_item_code = item.name
				ecomm_item.integration_item_code = item.amazon_item_code
				ecomm_item.has_variants = 0
				ecomm_item.sku = item.amazon_item_code
				ecomm_item.flags.ignore_mandatory = True
				ecomm_item.save(ignore_permissions=True)
