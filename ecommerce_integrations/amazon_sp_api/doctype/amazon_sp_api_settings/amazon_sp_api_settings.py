# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document

from ecommerce_integrations.amazon_sp_api.doctype.amazon_sp_api_settings.amazon_repository import (
	get_orders,
	get_products_details,
)


class AmazonSPAPISettings(Document):
	def validate(self):
		if self.enable_amazon == 1:
			setup_custom_fields()
		else:
			self.enable_sync = 0

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
