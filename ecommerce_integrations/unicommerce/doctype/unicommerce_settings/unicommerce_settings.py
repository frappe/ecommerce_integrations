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
	ITEM_SYNC_CHECKBOX,
	ORDER_CODE_FIELD,
	ORDER_STATUS_FIELD,
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

		# TODO: handle 30 days limit
		if not self.access_token or now_datetime() >= get_datetime(self.expires_on):
			try:
				self.update_tokens()
			except:
				create_unicommerce_log(status="Error", message="Failed to authenticate with Unicommerce")
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

		params = {
			"grant_type": grant_type,
			"client_id": "my-trusted-client",  # TODO: make this configurable
		}
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
			res = res.json()
			error, description = res.get("error"), res.get("error_description")
			frappe.throw(_("Unicommerce reported error: <br>{}: {}").format(error, description))

	def get_erpnext_warehouses(self) -> List[ERPNextWarehouse]:
		return [wh_map.erpnext_warehouse for wh_map in self.warehouse_mapping if wh_map.enabled]

	def get_erpnext_to_integration_wh_mapping(self) -> Dict[ERPNextWarehouse, IntegrationWarehouse]:
		"""Get enabled mapping from ERPNextWarehouse to Unicommerce facility."""
		return {
			wh_map.erpnext_warehouse: wh_map.unicommerce_facility_code
			for wh_map in self.warehouse_mapping
			if wh_map.enabled
		}

	def get_integration_to_erpnext_wh_mapping(self) -> Dict[IntegrationWarehouse, ERPNextWarehouse]:
		"""Get enabled mapping from Unicommerce facility to ERPNext warehouse."""
		reverse_map = self.get_erpnext_to_integration_wh_mapping()

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
				insert_after="update_auto_repeat_reference",
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
				fieldname=ORDER_STATUS_FIELD,
				label="Unicommerce Order Status",
				fieldtype="Data",
				insert_after=CHANNEL_ID_FIELD,
				read_only=1,
			),
		],
	}

	create_custom_fields(custom_fields)
