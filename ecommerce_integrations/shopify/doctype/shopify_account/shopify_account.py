# Copyright (c) 2021, Frappe and contributors
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
	ACCOUNT_DOCTYPE,
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	OAUTH_AUTH_METHOD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	STATIC_TOKEN_AUTH_METHOD,
	SUPPLIER_ID_FIELD,
)
from ecommerce_integrations.shopify.utils import (
	ensure_old_connector_is_disabled,
	migrate_from_old_connector,
	normalize_shop_url,
)


class ShopifyAccount(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_shopify)

	def autoname(self):
		# the account is named after the shop domain Shopify sends with its webhooks,
		# so normalise the URL before it becomes the name
		self.shopify_url = normalize_shop_url(self.shopify_url)
		self.name = self.shopify_url

	def validate(self):
		ensure_old_connector_is_disabled()

		if self.shopify_url:
			self.shopify_url = normalize_shop_url(self.shopify_url)
		self._validate_credentials()
		self._handle_webhooks()
		self._validate_warehouse_links()
		self._initalize_default_values()

		if self.is_enabled():
			setup_custom_fields()

	def on_update(self):
		if self.is_enabled() and not self.is_old_data_migrated:
			migrate_from_old_connector(shopify_account=self.name)

	def _validate_credentials(self):
		if not self.authentication_method:
			self.authentication_method = STATIC_TOKEN_AUTH_METHOD

		credentials_changed = self.is_new() or any(
			self.has_value_changed(fieldname)
			for fieldname in ("authentication_method", "client_id", "client_secret")
		)
		if credentials_changed:
			# a token issued for the previous credentials must not be used again, also when
			# they were changed while the account is disabled
			self.token_expires_at = None

		if not self.is_enabled():
			return

		if self.authentication_method == OAUTH_AUTH_METHOD:
			required = {"client_id": _("Client ID"), "client_secret": _("Client Secret")}
		else:
			required = {
				"password": _("Password / Access Token"),
				"shared_secret": _("Shared secret / API Secret"),
			}

		for fieldname, label in required.items():
			value = self.get(fieldname)
			if (
				self.meta.get_field(fieldname).fieldtype == "Password"
				and value
				and self.is_dummy_password(value)
			):
				continue
			if not value:
				frappe.throw(_("{0} is required for {1}.").format(label, self.authentication_method))

		if self.authentication_method == OAUTH_AUTH_METHOD and (
			credentials_changed or self.has_value_changed("enable_shopify")
		):
			# new credentials or a (re-)enabled account: get a token now, so wrong ones fail on save
			connection.get_access_token(self)

	def _handle_webhooks(self):
		if self.is_enabled() and not self.webhooks:
			new_webhooks = connection.register_webhooks(self.shopify_url, connection.get_access_token(self))

			if not new_webhooks:
				msg = _("Failed to register webhooks with Shopify.") + "<br>"
				msg += _("Please check credentials and retry.") + " "
				msg += _("Disabling and re-enabling the integration might also help.")
				frappe.throw(msg)

			for webhook in new_webhooks:
				self.append("webhooks", {"webhook_id": webhook.id, "method": webhook.topic})

		elif not self.is_enabled():
			# Disabling an account is how its credentials get corrected, so a failure to
			# unregister (e.g. 401 for a wrong token) must not refuse the save.
			if self.webhooks:
				try:
					connection.unregister_webhooks(self.shopify_url, connection.get_access_token(self))
				except Exception:
					frappe.log_error(title=_("Shopify: unregistering webhooks failed"))
					frappe.msgprint(
						_(
							"Could not unregister the webhooks from Shopify. Remove them in the Shopify admin if they remain."
						),
						alert=True,
					)

			self.webhooks = list()  # remove all webhooks

	def _validate_warehouse_links(self):
		for wh_map in self.shopify_warehouse_mapping:
			if not wh_map.erpnext_warehouse:
				frappe.throw(_("ERPNext warehouse required in warehouse map table."))

	def _initalize_default_values(self):
		if not self.last_inventory_sync:
			self.last_inventory_sync = get_datetime("1970-01-01")

	@frappe.whitelist()
	def update_location_table(self):
		"""Fetch locations from shopify and add it to child table so user can
		map it with correct ERPNext warehouse."""

		self.shopify_warehouse_mapping = []
		with connection.shopify_session(self):
			for locations in PaginatedIterator(Location.find()):
				for location in locations:
					self.append(
						"shopify_warehouse_mapping",
						{"shopify_location_id": location.id, "shopify_location_name": location.name},
					)

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
