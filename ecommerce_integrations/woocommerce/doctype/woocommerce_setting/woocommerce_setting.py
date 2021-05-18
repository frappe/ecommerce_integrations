# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of
from ecommerce_integrations.controllers.setting import SettingController
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from ecommerce_integrations.woocommerce.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.ecommerce_integrations.utils import get_current_domain_name

class WoocommerceSetting(SettingController):

	def is_enabled(self) -> bool:
		return bool(self.enable_sync)

	def validate(self):
		self.validate_setting()
		self.create_delete_custom_fields()
		self.create_webhook_url()

	def create_delete_custom_fields(self):
		if self.enable_sync:
			# create
			for doctype in ["Customer", "Sales Order", "Item", "Address"]:
				df = dict(fieldname='woocommerce_id', label='Woocommerce ID', fieldtype='Data', read_only=1, print_hide=1)
				create_custom_field(doctype, df)

			for doctype in ["Customer", "Address"]:
				df = dict(fieldname='woocommerce_email', label='Woocommerce Email', fieldtype='Data', read_only=1, print_hide=1)
				create_custom_field(doctype, df)

			if not frappe.get_value("Item Group", {"name": _("WooCommerce Products")}):
				item_group = frappe.new_doc("Item Group")
				item_group.item_group_name = _("WooCommerce Products")
				item_group.parent_item_group = get_root_of("Item Group")
				item_group.insert()

	def validate_setting(self):
		if self.enable_sync:
			if not self.secret:
				self.set("secret", frappe.generate_hash())

			if not self.woocommerce_server_url:
				frappe.throw(_("Please enter Woocommerce Server URL"))

			if not self.api_consumer_key:
				frappe.throw(_("Please enter API Consumer Key"))

			if not self.api_consumer_secret:
				frappe.throw(_("Please enter API Consumer Secret"))

	def create_webhook_url(self):
		host = get_current_domain_name()
		endpoint = "api/method/ecommerce_integrations.woocommerce.connection.order"

		self.endpoint = f"https://{host}/{endpoint}"

@frappe.whitelist()
def generate_secret():
	woocommerce_setting = frappe.get_doc(SETTING_DOCTYPE)
	woocommerce_setting.secret = frappe.generate_hash()
	woocommerce_setting.save()

@frappe.whitelist()
def get_series():
	return {
		"sales_order_series" : frappe.get_meta("Sales Order").get_options("naming_series") or "SO-WOO-",
	}
