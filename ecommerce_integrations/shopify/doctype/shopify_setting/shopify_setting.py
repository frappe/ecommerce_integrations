# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from pyactiveresource.connection import UnauthorizedAccess
from shopify.resources import Location

from ecommerce_integrations.controllers.setting import SettingController
from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.utils import migrate_from_old_connector
from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SUPPLIER_ID_FIELD,
)


class ShopifySetting(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_shopify)

	def validate(self):
		if not self.is_old_data_migrated:
			migrate_from_old_connector()

		self._handle_webhooks()
		self._validate_warehouse_links()

		if self.is_enabled():
			setup_custom_fields()

	def _handle_webhooks(self):
		if self.is_enabled() and not self.webhooks:
			try:
				new_webhooks = connection.register_webhooks(self.shopify_url, self.get_password("password"))
			except UnauthorizedAccess:
				new_webhooks = []

			if not new_webhooks:
				msg = _("Failed to register webhooks with Shopify.") + "<br>"
				msg += _("Please check credentials and retry. Disabling and re-enabling the integration might also help.")
				frappe.throw(msg)

			for webhook in new_webhooks:
				self.append("webhooks", {"webhook_id": webhook.id, "method": webhook.topic})

		elif not self.is_enabled():
			connection.unregister_webhooks(self.shopify_url, self.get_password("password"))

			self.webhooks = list()  # remove all webhooks

	def _validate_warehouse_links(self):
		for wh_map in self.shopify_warehouse_mapping:
			if not wh_map.erpnext_warehouse:
				frappe.throw(_("ERPNext warehouse required in warehouse map table."))

	@frappe.whitelist()
	@connection.temp_shopify_session
	def update_location_table(self):
		"""Fetch locations from shopify and add it to child table so user can
		map it with correct ERPNext warehouse."""

		locations = Location.find()

		self.shopify_warehouse_mapping = []
		for location in locations:
			self.append(
				"shopify_warehouse_mapping",
				{"shopify_location_id": location.id, "shopify_location_name": location.name},
			)


@frappe.whitelist()
def get_series():
	return {
		"sales_order_series": frappe.get_meta("Sales Order").get_options("naming_series")
		or "SO-Shopify-",
		"sales_invoice_series": frappe.get_meta("Sales Invoice").get_options("naming_series")
		or "SI-Shopify-",
		"delivery_note_series": frappe.get_meta("Delivery Note").get_options("naming_series")
		or "DN-Shopify-",
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
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Shopify Order Status",
				fieldtype="Data",
				insert_after=ORDER_NUMBER_FIELD,
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
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Shopify Order Status",
				fieldtype="Data",
				insert_after=ORDER_NUMBER_FIELD,
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
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Shopify Order Status",
				fieldtype="Data",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
	}

	create_custom_fields(custom_fields)
