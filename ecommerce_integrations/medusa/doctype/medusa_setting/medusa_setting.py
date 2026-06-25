# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import get_datetime

from ecommerce_integrations.controllers.setting import (
	ERPNextWarehouse,
	IntegrationWarehouse,
	SettingController,
)
from ecommerce_integrations.medusa.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_LINE_ID_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	RETURN_ID_FIELD,
	WEBHOOK_EVENTS,
)


class MedusaSetting(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_medusa)

	def validate(self):
		if self.medusa_url:
			self.medusa_url = self.medusa_url.rstrip("/")

		self._validate_warehouse_links()
		self._initialize_default_values()

		if self.is_enabled():
			self._populate_webhook_checklist()
			self._test_credentials()
			setup_custom_fields()

	def _validate_warehouse_links(self):
		for wh_map in self.medusa_warehouse_mapping:
			if not wh_map.erpnext_warehouse:
				frappe.throw(_("ERPNext warehouse required in warehouse map table."))

	def _initialize_default_values(self):
		if not self.last_inventory_sync:
			self.last_inventory_sync = get_datetime("1970-01-01")

	def _populate_webhook_checklist(self):
		"""Medusa cannot report webhook registration, so we surface the required
		topics as a read-only checklist the operator wires up in the subscriber."""
		if not self.webhooks:
			for topic in WEBHOOK_EVENTS:
				self.append("webhooks", {"topic": topic})

	def _test_credentials(self):
		"""Cheap call to verify the Medusa URL + Admin API key. Skipped in tests."""
		if frappe.flags.in_test:
			return
		from ecommerce_integrations.medusa.connection import MedusaClient

		try:
			MedusaClient(self.medusa_url, self.get_password("admin_api_key")).get(
				"/orders", params={"limit": 1}
			)
		except Exception:
			frappe.throw(
				_("Could not connect to Medusa. Please verify the URL and Admin API key.")
				+ "<br>"
				+ frappe.get_traceback(with_context=False)
			)

	@frappe.whitelist()
	def fetch_medusa_locations(self):
		"""Pull stock locations from Medusa so the operator can map them to warehouses."""
		from ecommerce_integrations.medusa.connection import MedusaClient

		self.medusa_warehouse_mapping = []
		for location in MedusaClient(
			self.medusa_url, self.get_password("admin_api_key")
		).list_stock_locations():
			self.append(
				"medusa_warehouse_mapping",
				{"medusa_location_id": location.get("id"), "medusa_location_name": location.get("name")},
			)
		# do NOT save here: validate() rejects mapping rows without an ERPNext warehouse.
		# The client refreshes the child table; the operator maps + saves the form.

	# -- SettingController warehouse-mapping interface ----------------------------

	def get_erpnext_warehouses(self) -> list[ERPNextWarehouse]:
		return [wh_map.erpnext_warehouse for wh_map in self.medusa_warehouse_mapping]

	def get_erpnext_to_integration_wh_mapping(self) -> dict[ERPNextWarehouse, IntegrationWarehouse]:
		return {
			wh_map.erpnext_warehouse: wh_map.medusa_location_id for wh_map in self.medusa_warehouse_mapping
		}

	def get_integration_to_erpnext_wh_mapping(self) -> dict[IntegrationWarehouse, ERPNextWarehouse]:
		return {
			wh_map.medusa_location_id: wh_map.erpnext_warehouse for wh_map in self.medusa_warehouse_mapping
		}


def _line_id_field(insert_after):
	return dict(
		fieldname=ORDER_LINE_ID_FIELD,
		label="Medusa Order Line Id",
		fieldtype="Data",
		insert_after=insert_after,
		read_only=1,
		print_hide=1,
	)


def setup_custom_fields():
	order_fields = [
		dict(
			fieldname=ORDER_ID_FIELD,
			label="Medusa Order Id",
			fieldtype="Small Text",
			insert_after="title",
			read_only=1,
			print_hide=1,
		),
		dict(
			fieldname=ORDER_NUMBER_FIELD,
			label="Medusa Display Id",
			fieldtype="Small Text",
			insert_after=ORDER_ID_FIELD,
			read_only=1,
			print_hide=1,
		),
		dict(
			fieldname=ORDER_STATUS_FIELD,
			label="Medusa Order Status",
			fieldtype="Small Text",
			insert_after=ORDER_NUMBER_FIELD,
			read_only=1,
			print_hide=1,
		),
	]

	custom_fields = {
		"Item": [
			dict(
				fieldname=ITEM_SELLING_RATE_FIELD,
				label="Medusa Selling Rate",
				fieldtype="Currency",
				insert_after="standard_rate",
			)
		],
		"Customer": [
			dict(
				fieldname=CUSTOMER_ID_FIELD,
				label="Medusa Customer Id",
				fieldtype="Data",
				insert_after="series",
				read_only=1,
				print_hide=1,
			)
		],
		"Address": [
			dict(
				fieldname=ADDRESS_ID_FIELD,
				label="Medusa Address Id",
				fieldtype="Data",
				insert_after="fax",
				read_only=1,
				print_hide=1,
			)
		],
		"Sales Order": list(order_fields),
		"Sales Order Item": [
			dict(
				fieldname=ORDER_ITEM_DISCOUNT_FIELD,
				label="Medusa Discount per unit",
				fieldtype="Float",
				insert_after="discount_and_margin",
				read_only=1,
			),
			_line_id_field("item_code"),
		],
		"Sales Invoice Item": [_line_id_field("item_code")],
		"Delivery Note Item": [_line_id_field("item_code")],
		"Delivery Note": [
			*order_fields,
			dict(
				fieldname=FULFILLMENT_ID_FIELD,
				label="Medusa Fulfillment Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice": [
			*order_fields,
			dict(
				fieldname=RETURN_ID_FIELD,
				label="Medusa Return Id",
				fieldtype="Small Text",
				insert_after=ORDER_STATUS_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
	}

	create_custom_fields(custom_fields)
