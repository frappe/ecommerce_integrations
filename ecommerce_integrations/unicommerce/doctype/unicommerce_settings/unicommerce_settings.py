# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

from typing import Dict, List, Optional, Tuple

import frappe
import requests
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import add_to_date, get_datetime, now_datetime

from ecommerce_integrations.controllers.setting import (
	ERPNextWarehouse,
	IntegrationWarehouse,
	SettingController,
)
from ecommerce_integrations.unicommerce.constants import (
	ADDRESS_JSON_FIELD,
	CHANNEL_ID_FIELD,
	CUSTOMER_CODE_FIELD,
	FACILITY_CODE_FIELD,
	GRN_STOCK_ENTRY_TYPE,
	INVOICE_CODE_FIELD,
	IS_COD_CHECKBOX,
	ITEM_BATCH_GROUP_FIELD,
	ITEM_HEIGHT_FIELD,
	ITEM_LENGTH_FIELD,
	ITEM_SYNC_CHECKBOX,
	ITEM_WIDTH_FIELD,
	MANIFEST_GENERATED_CHECK,
	ORDER_CODE_FIELD,
	ORDER_INVOICE_STATUS_FIELD,
	ORDER_ITEM_BATCH_NO,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	PACKAGE_TYPE_FIELD,
	PICKLIST_ORDER_DETAILS_FIELD,
	PRODUCT_CATEGORY_FIELD,
	RETURN_CODE_FIELD,
	SHIPPING_METHOD_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PACKAGE_STATUS_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
	UNICOMMERCE_SHIPPING_ID,
)
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log


class UnicommerceSettings(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_unicommerce)

	def validate(self):
		if not self.is_enabled():
			self.access_token = ""
			self.refresh_token = ""
			self.token_type = ""
			self.expires_on = now_datetime()
			return

		self.validate_warehouse_mapping()
		self.validate_auto_grn_settings()
		if not self.access_token or now_datetime() >= get_datetime(self.expires_on):
			try:
				self.update_tokens()
			except Exception as e:
				create_unicommerce_log(
					status="Error", message="Failed to authenticate with Unicommerce", exception=e
				)

		if not self.flags.ignore_custom_fields:
			setup_custom_fields(update=False)

	def renew_tokens(self, save=True):
		if now_datetime() >= get_datetime(self.expires_on):
			try:
				self.update_tokens()
			except Exception as e:
				create_unicommerce_log(status="Error", message="Failed to authenticate with Unicommerce")
				raise e
		if save:
			self.flags.ignore_custom_fields = True
			self.flags.ignore_permissions = True
			self.save()
			frappe.db.commit()
			self.load_from_db()

	def update_tokens(self, grant_type="password"):
		url = f"https://{self.unicommerce_site}/oauth/token"

		params = {"grant_type": grant_type, "client_id": self.client_id}
		if grant_type == "password":
			params.update({"username": self.username, "password": self.get_password("password")})
		elif grant_type == "refresh_token":
			params.update({"refresh_token": self.get_password("refresh_token")})

		res = requests.get(url, params=params)
		if res.status_code == 200:
			res = res.json()
			self.access_token = res["access_token"]
			self.refresh_token = res["refresh_token"]
			self.token_type = res["token_type"]
			self.expires_on = add_to_date(now_datetime(), seconds=int(res["expires_in"]))
		else:
			# Invalid refresh token
			res = res.json()
			error, description = res.get("error"), res.get("error_description")
			if error and "invalid_grant" in error:
				self._handle_refresh_token_expiry(grant_type=grant_type)
			else:
				frappe.throw(_("Unicommerce reported error: <br>{}: {}").format(error, description))

	def _handle_refresh_token_expiry(self, grant_type: str):
		"""Handle expired refresh token. Refresh tokens expire every 30 days.

		This is only notified using `invalid_grant` in error message."""

		if grant_type == "password":
			return
		self.update_tokens(grant_type="password")

	def validate_auto_grn_settings(self):
		if not self.use_stock_entry_for_grn:
			return

		if not self.vendor_code:
			frappe.throw(_("Vendor code required for Auto GRN upload."))

		if not frappe.db.exists("Stock Entry Type", GRN_STOCK_ENTRY_TYPE):
			entry_type = frappe.new_doc("Stock Entry Type")
			entry_type.name = GRN_STOCK_ENTRY_TYPE
			entry_type.purpose = "Material Transfer"
			entry_type.insert()
			entry_type.add_comment(text="Entry type used for Auto GRN on unicommerce, do not modify.")

	def validate_warehouse_mapping(self):
		erpnext_whs = {wh_map.erpnext_warehouse for wh_map in self.warehouse_mapping}
		integration_whs = {wh_map.unicommerce_facility_code for wh_map in self.warehouse_mapping}

		if len(erpnext_whs) != len(integration_whs):
			frappe.throw(
				_("Warehouse Mapping should be unique and one-to-one without repeating same warehouses.")
			)

	def get_erpnext_warehouses(self, all_wh=False) -> List[ERPNextWarehouse]:
		"""Get list of configured ERPNext warehouses.

		all_wh flag ignores enabled status.
		"""
		return [
			wh_map.erpnext_warehouse for wh_map in self.warehouse_mapping if wh_map.enabled or all_wh
		]

	def get_erpnext_to_integration_wh_mapping(
		self, all_wh=False
	) -> Dict[ERPNextWarehouse, IntegrationWarehouse]:
		"""Get enabled mapping from ERPNextWarehouse to Unicommerce facility.

		all_wh flag ignores enabled status."""
		return {
			wh_map.erpnext_warehouse: wh_map.unicommerce_facility_code
			for wh_map in self.warehouse_mapping
			if wh_map.enabled or all_wh
		}

	def get_integration_to_erpnext_wh_mapping(
		self, all_wh=False
	) -> Dict[IntegrationWarehouse, ERPNextWarehouse]:
		"""Get enabled mapping from Unicommerce facility to ERPNext warehouse.

		all_wh flag ignores enabled status."""
		reverse_map = self.get_erpnext_to_integration_wh_mapping(all_wh=all_wh)

		return {v: k for k, v in reverse_map.items()}

	def get_company_addresses(self, facility_code: str) -> Tuple[Optional[str], Optional[str]]:
		""" Get mapped company billing and shipping addresses."""
		for wh_map in self.warehouse_mapping:
			if wh_map.unicommerce_facility_code == facility_code:
				return wh_map.company_address, wh_map.dispatch_address
		return None, None


def setup_custom_fields(update=True):

	custom_sections = {
		"Sales Order": [
			dict(
				fieldname="unicommerce_section",
				label="Unicommerce Details",
				fieldtype="Section Break",
				insert_after="update_auto_repeat_reference",
				collapsible=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname="unicommerce_section",
				label="Unicommerce Details",
				fieldtype="Section Break",
				insert_after="against_income_account",
				collapsible=1,
			),
		],
		"Delivery Note": [
			dict(
				fieldname="unicommerce_section",
				label="Unicommerce Details",
				fieldtype="Section Break",
				insert_after="instructions",
				collapsible=1,
			),
		],
	}

	custom_fields = {
		"Item": [
			dict(
				fieldname=ITEM_SYNC_CHECKBOX,
				label="Sync Item with Unicommerce",
				fieldtype="Check",
				insert_after="item_code",
				print_hide=1,
			),
			dict(
				fieldname=ITEM_LENGTH_FIELD,
				label="Length (mm) (Unicommerce)",
				fieldtype="Int",
				insert_after="over_billing_allowance",
				print_hide=1,
			),
			dict(
				fieldname=ITEM_WIDTH_FIELD,
				label="Width (mm) (Unicommerce)",
				fieldtype="Int",
				insert_after=ITEM_LENGTH_FIELD,
				print_hide=1,
			),
			dict(
				fieldname=ITEM_HEIGHT_FIELD,
				label="Height (mm) (Unicommerce)",
				fieldtype="Int",
				insert_after=ITEM_WIDTH_FIELD,
				print_hide=1,
			),
			dict(
				fieldname=ITEM_BATCH_GROUP_FIELD,
				label="Batch Group Code",
				fieldtype="Data",
				insert_after=ITEM_HEIGHT_FIELD,
				print_hide=1,
			),
		],
		"Sales Order": [
			dict(
				fieldname=ORDER_CODE_FIELD,
				label="Unicommerce Order No.",
				fieldtype="Data",
				insert_after="unicommerce_section",
				read_only=1,
				search_index=1,
			),
			dict(
				fieldname=CHANNEL_ID_FIELD,
				label="Unicommerce Channel",
				fieldtype="Link",
				insert_after=ORDER_CODE_FIELD,
				read_only=1,
				options="Unicommerce Channel",
				search_index=1,
			),
			dict(
				fieldname=FACILITY_CODE_FIELD,
				label="Unicommerce Facility Code",
				fieldtype="Small Text",
				insert_after=CHANNEL_ID_FIELD,
				read_only=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Unicommerce Order Status",
				fieldtype="Small Text",
				insert_after=FACILITY_CODE_FIELD,
				read_only=1,
			),
			dict(
				fieldname=ORDER_INVOICE_STATUS_FIELD,
				label="Unicommerce Invoice generation Status",
				fieldtype="Small Text",
				insert_after=ORDER_STATUS_FIELD,
				read_only=1,
			),
			dict(
				fieldname=PACKAGE_TYPE_FIELD,
				label="Unicommerce Package Type",
				fieldtype="Link",
				options="Unicommerce Package Type",
				insert_after=ORDER_INVOICE_STATUS_FIELD,
				allow_on_submit=1,
			),
		],
		"Sales Order Item": [
			dict(
				fieldname=ORDER_ITEM_CODE_FIELD,
				label="Unicommerce Order Item Code",
				fieldtype="Data",
				insert_after="item_code",
				read_only=1,
			),
			dict(
				fieldname=ORDER_ITEM_BATCH_NO,
				label="Unicommerce Batch Code",
				fieldtype="Data",
				insert_after=ORDER_ITEM_CODE_FIELD,
				read_only=1,
			),
		],
		"Item Group": [
			dict(
				fieldname=PRODUCT_CATEGORY_FIELD,
				label="Unicommerce Product Category Code",
				fieldtype="Data",
				insert_after="is_group",
				unique=1,
			),
		],
		"Customer": [
			dict(
				fieldname=ADDRESS_JSON_FIELD,
				label="Unicommerce raw billing address",
				fieldtype="Text",
				insert_after="append",
				read_only=1,
				hidden=1,
			),
			dict(
				fieldname=CUSTOMER_CODE_FIELD,
				label="Unicommerce customer code",
				fieldtype="Data",
				insert_after="naming_series",
				read_only=1,
			),
			dict(
				fieldname=IS_COD_CHECKBOX,
				label="Is COD?",
				fieldtype="Check",
				insert_after=CUSTOMER_CODE_FIELD,
				read_only=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname=ORDER_CODE_FIELD,
				label="Unicommerce Order No.",
				fieldtype="Data",
				insert_after="unicommerce_section",
				read_only=1,
				search_index=1,
			),
			dict(
				fieldname=CHANNEL_ID_FIELD,
				label="Unicommerce Channel",
				fieldtype="Link",
				insert_after=ORDER_CODE_FIELD,
				read_only=1,
				options="Unicommerce Channel",
				search_index=1,
			),
			dict(
				fieldname=FACILITY_CODE_FIELD,
				label="Unicommerce Facility Code",
				fieldtype="Small Text",
				insert_after=CHANNEL_ID_FIELD,
				read_only=1,
			),
			dict(
				fieldname=INVOICE_CODE_FIELD,
				label="Unicommerce Invoice Code",
				fieldtype="Data",
				insert_after=FACILITY_CODE_FIELD,
				read_only=1,
				search_index=1,
			),
			dict(
				fieldname=SHIPPING_PACKAGE_CODE_FIELD,
				label="Unicommerce Shipping Package Code",
				fieldtype="Small Text",
				insert_after=INVOICE_CODE_FIELD,
				read_only=1,
			),
			dict(
				fieldname=SHIPPING_PROVIDER_CODE,
				label="Unicommerce Shipping Provider",
				fieldtype="Small Text",
				insert_after=SHIPPING_PACKAGE_CODE_FIELD,
				read_only=1,
			),
			dict(
				fieldname=SHIPPING_METHOD_FIELD,
				label="Unicommerce Shipping Method",
				fieldtype="Small Text",
				insert_after=SHIPPING_PROVIDER_CODE,
				read_only=1,
			),
			dict(
				fieldname=TRACKING_CODE_FIELD,
				label="Unicommerce Tracking Code",
				fieldtype="Small Text",
				insert_after=SHIPPING_METHOD_FIELD,
				read_only=1,
			),
			dict(
				fieldname=SHIPPING_PACKAGE_STATUS_FIELD,
				label="Unicommerce Package Status",
				fieldtype="Small Text",
				insert_after=TRACKING_CODE_FIELD,
				read_only=1,
			),
			dict(
				fieldname=MANIFEST_GENERATED_CHECK,
				label="Manifest generated",
				fieldtype="Check",
				insert_after=SHIPPING_PACKAGE_STATUS_FIELD,
				read_only=1,
			),
			dict(
				fieldname=IS_COD_CHECKBOX,
				label="Is COD?",
				fieldtype="Check",
				insert_after=MANIFEST_GENERATED_CHECK,
				read_only=1,
			),
			dict(
				fieldname=RETURN_CODE_FIELD,
				label="Unicommerce Return Code",
				fieldtype="Small Text",
				insert_after=IS_COD_CHECKBOX,
				read_only=1,
			),
		],
		"Delivery Note": [
			dict(
				fieldname=ORDER_CODE_FIELD,
				label="Unicommerce Order No",
				fieldtype="Data",
				insert_after="unicommerce_section",
				read_only=1,
			),
			dict(
				fieldname=UNICOMMERCE_SHIPPING_ID,
				label="Unicommerce Shipment Id",
				fieldtype="Data",
				insert_after=ORDER_CODE_FIELD,
				read_only=1,
			),
		],
		"Pick List": [
			dict(
				fieldname=PICKLIST_ORDER_DETAILS_FIELD,
				label="Order Details",
				fieldtype="Table",
				options="Pick List Sales Order Details",
			),
		],
	}

	# create sections first for proper ordering
	create_custom_fields(custom_sections, update=update)
	create_custom_fields(custom_fields, update=update)
