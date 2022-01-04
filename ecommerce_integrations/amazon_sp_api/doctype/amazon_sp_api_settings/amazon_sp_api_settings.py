# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document


class AmazonSPAPISettings(Document):
	def validate(self):
		if self.enable_amazon == 1:
			self.enable_sync = 1
			setup_custom_fields()
		else:
			self.enable_sync = 0

	@frappe.whitelist()
	def get_products_details(self):
		pass

	@frappe.whitelist()
	def get_order_details(self):
		pass


def setup_custom_fields():
	custom_fields = {
		"Item": [
			dict(
				fieldname="amazon_item_code",
				label="Amazon Item Code",
				fieldtype="Data",
				insert_after="series",
				read_only=1,
				print_hide=1,
			)
		],
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
