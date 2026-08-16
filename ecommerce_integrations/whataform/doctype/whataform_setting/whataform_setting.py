# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

from typing import Dict, List

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import get_datetime

from ecommerce_integrations.controllers.setting import (
	ERPNextWarehouse,
	IntegrationWarehouse,
	SettingController,
)
from ecommerce_integrations.whataform.connection import get_callback_url
from ecommerce_integrations.whataform.constants import (
	CUSTOMER_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
)


class WhataformSetting(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_whataform)

	@property
	def webhook_url(self):
		if frappe.request:
			return get_callback_url()

	def validate(self):
		self._validate_warehouse_links()

		if self.is_enabled():
			setup_custom_fields()

	def _validate_warehouse_links(self):
		for wh_map in self.whataform_warehouse_mapping:
			if not wh_map.erpnext_warehouse:
				frappe.throw(_("ERPNext warehouse required in warehouse map table."))

	def get_erpnext_warehouses(self) -> List[ERPNextWarehouse]:
		return [wh_map.erpnext_warehouse for wh_map in self.whataform_warehouse_mapping]

	def get_erpnext_to_integration_wh_mapping(self) -> Dict[ERPNextWarehouse, IntegrationWarehouse]:
		return {
			wh_map.erpnext_warehouse: wh_map.whataform_location_id
			for wh_map in self.whataform_warehouse_mapping
		}

	def get_integration_to_erpnext_wh_mapping(self) -> Dict[IntegrationWarehouse, ERPNextWarehouse]:
		return {
			wh_map.whataform_location_id: wh_map.erpnext_warehouse
			for wh_map in self.whataform_warehouse_mapping
		}


def setup_custom_fields():
	custom_fields = {
		"Customer": [
			dict(
				fieldname=CUSTOMER_ID_FIELD,
				label="Whataform Customer Id",
				fieldtype="Data",
				insert_after="series",
				read_only=1,
				print_hide=1,
			)
		],
		"Sales Order": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Whataform Order Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Whataform Order Number",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Order Item": [
			dict(
				fieldname=ORDER_ITEM_DISCOUNT_FIELD,
				label="Whataform Discount per unit",
				fieldtype="Float",
				insert_after="discount_and_margin",
				read_only=1,
			),
		],
		"Delivery Note": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Whataform Order Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Whataform Order Number",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Whataform Order Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Whataform Order Number",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
	}

	create_custom_fields(custom_fields)
