# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ecommerce_integrations.controllers.setting import SettingController
from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.constants import (
	CUSTOMER_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	FULLFILLMENT_ID_FIELD,
	ADDRESS_ID_FIELD,
	SUPPLIER_ID_FIELD,
)


class ShopifySetting(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_shopify)

	def validate(self):
		self.handle_webhooks()

	def handle_webhooks(self):
		if self.is_enabled() and not self.webhooks:
			setup_custom_fields()
			new_webhooks = connection.register_webhooks()

			for webhook in new_webhooks:
				self.append("webhooks", {"webhook_id": webhook.id, "method": webhook.topic})

		elif not self.is_enabled():
			connection.unregister_webhooks()

			self.webhooks = list()  # remove all webhooks


@frappe.whitelist()
def get_series():
	return {
		"sales_order_series" : frappe.get_meta("Sales Order").get_options("naming_series") or "SO-Shopify-",
		"sales_invoice_series" : frappe.get_meta("Sales Invoice").get_options("naming_series")  or "SI-Shopify-",
		"delivery_note_series" : frappe.get_meta("Delivery Note").get_options("naming_series")  or "DN-Shopify-"
	}


def setup_custom_fields():
	custom_fields = {
		"Customer": [
			dict(
				fieldname=CUSTOMER_ID_FIELD,
				label="Shopify Customer Id",
				fieldtype="Data",
				insert_after="series",
				read_only=1,
				print_hide=1,
			)
		],
		"Supplier": [
			dict(
				fieldname=SUPPLIER_ID_FIELD,
				label="Shopify Supplier Id",
				fieldtype="Data",
				insert_after="supplier_name",
				read_only=1,
				print_hide=1,
			)
		],
		"Address": [
			dict(
				fieldname=ADDRESS_ID_FIELD,
				label="Shopify Address Id",
				fieldtype="Data",
				insert_after="fax",
				read_only=1,
				print_hide=1,
			)
		],
		"Sales Order": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Shopify Order Id",
				fieldtype="Data",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Shopify Order Number",
				fieldtype="Data",
				insert_after="shopify_order_id",
				read_only=1,
				print_hide=1,
			),
		],
		"Delivery Note": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Shopify Order Id",
				fieldtype="Data",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Shopify Order Number",
				fieldtype="Data",
				insert_after="shopify_order_id",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=FULLFILLMENT_ID_FIELD,
				label="Shopify Fulfillment Id",
				fieldtype="Data",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Shopify Order Id",
				fieldtype="Data",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Shopify Order Number",
				fieldtype="Data",
				insert_after="shopify_order_id",
				read_only=1,
				print_hide=1,
			),
		],
	}

	create_custom_fields(custom_fields)
