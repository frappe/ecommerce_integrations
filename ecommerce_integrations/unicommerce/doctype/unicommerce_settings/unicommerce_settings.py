# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

from typing import Dict, List

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
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	ITEM_SYNC_CHECKBOX,
	ORDER_CODE_FIELD,
	ORDER_ITEM_CODE_FIELD,
	ORDER_STATUS_FIELD,
	PRODUCT_CATEGORY_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
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

		if not self.access_token or now_datetime() >= get_datetime(self.expires_on):
			try:
				self.update_tokens()
			except Exception as e:
				create_unicommerce_log(
					status="Error", message="Failed to authenticate with Unicommerce", exception=e
				)
		setup_custom_fields()

	def renew_tokens(self, save=True):
		if now_datetime() >= get_datetime(self.expires_on):
			try:
				self.update_tokens(grant_type="refresh_token")
			except Exception as e:
				create_unicommerce_log(status="Error", message="Failed to authenticate with Unicommerce")
				raise e
		if save:
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


def setup_custom_fields():
	custom_fields = {
		"Item": [
			dict(
				fieldname=ITEM_SYNC_CHECKBOX,
				label="Sync Item with Unicommerce",
				fieldtype="Check",
				insert_after="item_code",
				print_hide=1,
			)
		],
		"Sales Order": [
			dict(
				fieldname="unicommerce_section",
				label="Unicommerce Details",
				fieldtype="Section Break",
				insert_after="append",
				collapsible=1,
			),
			dict(
				fieldname=ORDER_CODE_FIELD,
				label="Unicommerce Order No.",
				fieldtype="Data",
				insert_after="unicommerce_section",
				read_only=1,
			),
			dict(
				fieldname=CHANNEL_ID_FIELD,
				label="Unicommerce Channel",
				fieldtype="Link",
				insert_after=ORDER_CODE_FIELD,
				read_only=1,
				options="Unicommerce Channel",
			),
			dict(
				fieldname=FACILITY_CODE_FIELD,
				label="Unicommerce Facility Code",
				fieldtype="Data",
				insert_after=CHANNEL_ID_FIELD,
				read_only=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Unicommerce Order Status",
				fieldtype="Data",
				insert_after=CHANNEL_ID_FIELD,
				read_only=1,
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
		"Sales Invoice": [
			dict(
				fieldname="unicommerce_section",
				label="Unicommerce Details",
				fieldtype="Section Break",
				insert_after="append",
				collapsible=1,
			),
			dict(
				fieldname=ORDER_CODE_FIELD,
				label="Unicommerce Order No.",
				fieldtype="Data",
				insert_after="unicommerce_section",
				read_only=1,
			),
			dict(
				fieldname=FACILITY_CODE_FIELD,
				label="Unicommerce Facility Code",
				fieldtype="Data",
				insert_after=ORDER_CODE_FIELD,
				read_only=1,
			),
			dict(
				fieldname=INVOICE_CODE_FIELD,
				label="Unicommerce Invoice Code",
				fieldtype="Data",
				insert_after=FACILITY_CODE_FIELD,
				read_only=1,
			),
			dict(
				fieldname=SHIPPING_PACKAGE_CODE_FIELD,
				label="Unicommerce Shipping Package Code",
				fieldtype="Data",
				insert_after=INVOICE_CODE_FIELD,
				read_only=1,
			),
		],
	}

	create_custom_fields(custom_fields)
