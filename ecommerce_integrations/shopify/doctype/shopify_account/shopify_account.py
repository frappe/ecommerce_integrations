# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import get_datetime
from shopify.collection import PaginatedIterator
from shopify.resources import Location

from ecommerce_integrations.controllers.setting import (
	ERPNextWarehouse,
	IntegrationWarehouse,
	SettingController,
)
from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SUPPLIER_ID_FIELD,
)
from ecommerce_integrations.shopify.oauth import validate_oauth_credentials
from ecommerce_integrations.shopify.utils import (
	ensure_old_connector_is_disabled,
	migrate_from_old_connector,
)


class ShopifyAccount(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_shopify)

	def _get_password_safe(self, fieldname: str) -> str:
		"""Get a password field without raising on new/unsaved docs."""
		try:
			if not self.name or self.is_new():
				return ""
			password = self.get_password(fieldname, raise_exception=False)
			return password if password else ""
		except Exception:
			return ""

	def validate(self):
		# TODO: uncomment
		# ensure_old_connector_is_disabled()

		if self.shopify_url:
			self.shopify_url = self.shopify_url.replace("https://", "").replace("http://", "")

		self._set_default_authentication_method()
		self._validate_authentication_fields()
		self._validate_oauth_credentials_if_needed()
		self._handle_webhooks()
		self._validate_warehouse_links()
		self._initalize_default_values()

		if self.is_enabled():
			setup_custom_fields()

	def on_update(self):
		if self.is_enabled() and not self.is_old_data_migrated:
			migrate_from_old_connector()

	def before_save(self):
		"""Pre-generate the OAuth token on credential changes for better UX. Falls back to on-demand."""
		if not self.is_enabled():
			return

		if self.authentication_method == "OAuth 2.0 Client Credentials":
			current_token = self._get_password_safe("oauth_access_token")
			if (
				self.has_value_changed("client_id")
				or self.has_value_changed("client_secret")
				or not current_token
			):
				try:
					self._get_or_generate_oauth_token()
				except Exception:
					pass

	def _set_default_authentication_method(self):
		if not self.authentication_method:
			self.authentication_method = "Static Token"

	def _validate_authentication_fields(self):
		if not self.is_enabled():
			return

		if self.authentication_method == "Static Token":
			if not self._get_password_safe("password"):
				frappe.throw(_("Password / Access Token is required for Static Token authentication"))
			if not self.shared_secret:
				frappe.throw(_("Shared secret / API Secret is required for Static Token authentication"))

		elif self.authentication_method == "OAuth 2.0 Client Credentials":
			if not self.client_id:
				frappe.throw(_("Client ID is required for OAuth 2.0 authentication"))
			if not self._get_password_safe("client_secret"):
				frappe.throw(_("Client Secret is required for OAuth 2.0 authentication"))

	def _validate_oauth_credentials_if_needed(self):
		if not self.is_enabled():
			return
		if self.authentication_method != "OAuth 2.0 Client Credentials":
			return

		if self.has_value_changed("client_id") or self.has_value_changed("client_secret"):
			client_secret = self._get_password_safe("client_secret")
			if not client_secret:
				return  # caught by _validate_authentication_fields

			validate_oauth_credentials(self.shopify_url, self.client_id, client_secret)
			frappe.msgprint(
				_("OAuth credentials validated successfully. Token will be auto-generated on save."),
				indicator="green",
				alert=True,
			)

	def _get_or_generate_oauth_token(self) -> str:
		"""Return a valid OAuth token, refreshing if missing/expired."""
		from ecommerce_integrations.shopify.oauth import is_token_valid, refresh_oauth_token

		current_token = self._get_password_safe("oauth_access_token")
		if current_token and is_token_valid(self.token_expires_at):
			return current_token

		try:
			return refresh_oauth_token(self)
		except Exception as e:
			frappe.throw(
				_("Failed to generate OAuth token: {0}").format(str(e)),
				title=_("OAuth Authentication Error"),
			)

	def _get_webhook_password(self) -> str:
		"""Return the access token to use when calling Shopify for webhook ops."""
		if self.authentication_method == "OAuth 2.0 Client Credentials":
			return self._get_or_generate_oauth_token()
		return self.get_password("password")

	def _handle_webhooks(self):
		if self.is_enabled() and not self.webhooks:
			new_webhooks = connection.register_webhooks(self.shopify_url, self._get_webhook_password())

			if not new_webhooks:
				msg = _("Failed to register webhooks with Shopify.") + "<br>"
				msg += _("Please check credentials and retry.") + " "
				msg += _("Disabling and re-enabling the integration might also help.")
				frappe.throw(msg)

			for webhook in new_webhooks:
				self.append("webhooks", {"webhook_id": webhook.id, "method": webhook.topic})

		elif not self.is_enabled():
			# Use whichever token we still have; for OAuth use the cached one (don't refresh on disable)
			if self.authentication_method == "OAuth 2.0 Client Credentials":
				password = self._get_password_safe("oauth_access_token")
			else:
				password = self._get_password_safe("password")

			if password:
				connection.unregister_webhooks(self.shopify_url, password)

			self.webhooks = list()  # remove all webhooks

	def _validate_warehouse_links(self):
		for wh_map in self.shopify_warehouse_mapping:
			if not wh_map.erpnext_warehouse:
				frappe.throw(_("ERPNext warehouse required in warehouse map table."))

	def _initalize_default_values(self):
		if not self.last_inventory_sync:
			self.last_inventory_sync = get_datetime("1970-01-01")

	@frappe.whitelist()
	@connection.temp_shopify_session(lambda self: self.shopify_url)
	def update_location_table(self):
		"""Fetch locations from shopify and add it to child table so user can
		map it with correct ERPNext warehouse."""

		self.shopify_warehouse_mapping = []
		for locations in PaginatedIterator(Location.find()):
			for location in locations:
				self.append(
					"shopify_warehouse_mapping",
					{"shopify_location_id": location.id, "shopify_location_name": location.name},
				)

	def get_shopify_locations(self):
		"""Fetch locations from shopify and add it to child table so user can
		map it with correct ERPNext warehouse."""
		result = []
		with connection.get_temp_session_context(self):
			for locations in PaginatedIterator(Location.find()):
				for location in locations:
					result.append(location)
			return result

	def get_erpnext_warehouses(self) -> list[ERPNextWarehouse]:
		return [wh_map.erpnext_warehouse for wh_map in self.shopify_warehouse_mapping]

	def get_erpnext_to_integration_wh_mapping(self) -> dict[ERPNextWarehouse, IntegrationWarehouse]:
		return {
			wh_map.erpnext_warehouse: wh_map.shopify_location_id for wh_map in self.shopify_warehouse_mapping
		}

	def get_integration_to_erpnext_wh_mapping(self) -> dict[IntegrationWarehouse, ERPNextWarehouse]:
		return {
			wh_map.shopify_location_id: wh_map.erpnext_warehouse for wh_map in self.shopify_warehouse_mapping
		}


def setup_custom_fields():
	custom_fields = {
		"Item": [
			dict(
				fieldname=ITEM_SELLING_RATE_FIELD,
				label="Shopify Selling Rate",
				fieldtype="Currency",
				insert_after="standard_rate",
			)
		],
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
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Shopify Order Number",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Shopify Order Status",
				fieldtype="Small Text",
				insert_after=ORDER_NUMBER_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Order Item": [
			dict(
				fieldname=ORDER_ITEM_DISCOUNT_FIELD,
				label="Shopify Discount per unit",
				fieldtype="Float",
				insert_after="discount_and_margin",
				read_only=1,
			),
		],
		"Delivery Note": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Shopify Order Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Shopify Order Number",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Shopify Order Status",
				fieldtype="Small Text",
				insert_after=ORDER_NUMBER_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=FULLFILLMENT_ID_FIELD,
				label="Shopify Fulfillment Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
		],
		"Sales Invoice": [
			dict(
				fieldname=ORDER_ID_FIELD,
				label="Shopify Order Id",
				fieldtype="Small Text",
				insert_after="title",
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_NUMBER_FIELD,
				label="Shopify Order Number",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
			dict(
				fieldname=ORDER_STATUS_FIELD,
				label="Shopify Order Status",
				fieldtype="Small Text",
				insert_after=ORDER_ID_FIELD,
				read_only=1,
				print_hide=1,
			),
		],
	}

	create_custom_fields(custom_fields)
