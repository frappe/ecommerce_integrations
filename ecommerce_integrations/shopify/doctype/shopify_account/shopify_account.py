# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

from collections.abc import Callable

import frappe
import requests
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
	AUTH_METHOD_OAUTH,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SUPPLIER_ID_FIELD,
)
from ecommerce_integrations.shopify.oauth import (
	get_valid_access_token,
	normalize_shopify_url,
	refresh_oauth_token,
)
from ecommerce_integrations.shopify.utils import (
	create_shopify_log,
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
			# a bare shop name is completed to its myshopify domain, then the URL is
			# validated strictly
			self.shopify_url = normalize_shopify_url(normalize_shop_url(self.shopify_url))

		self._validate_single_enabled_account()
		self._validate_authentication_fields()
		self._validate_warehouse_links()
		self._initalize_default_values()
		self._handle_webhooks()  # last: it calls Shopify

		if self.is_enabled():
			setup_custom_fields()

	def before_save(self):
		if (
			self.is_enabled()
			and self.authentication_method == AUTH_METHOD_OAUTH
			and not self.flags.oauth_token_refreshed_during_save
		):
			self._pregenerate_oauth_token()

		self._preserve_stored_oauth_token()

	def _pregenerate_oauth_token(self):
		"""Mint the OAuth token on save so credential mistakes surface immediately."""
		try:
			self._get_or_generate_oauth_token()
		except (frappe.ValidationError, requests.exceptions.RequestException) as e:
			# rejected new credentials must block the save; a Shopify outage must not
			if isinstance(e, frappe.ValidationError) and self._oauth_credentials_changed():
				raise

			create_shopify_log(
				status="Warning",
				method="ecommerce_integrations.shopify.doctype.shopify_account.shopify_account.before_save",
				shopify_account=self.name,
				message=_("Token pre-generation failed on save; will retry on first sync."),
				exception=str(e),
			)

	def on_update(self):
		if self.is_enabled() and not self.is_old_data_migrated:
			migrate_from_old_connector(shopify_account=self.name)

	def _validate_single_enabled_account(self):
		"""Incoming webhooks are attributed to the only enabled account, so a second
		enabled account would leave the webhooks of both stores unattributable."""
		if not self.is_enabled():
			return

		other_account = frappe.db.exists(
			ACCOUNT_DOCTYPE, {"enable_shopify": 1, "name": ("!=", self.name or "")}
		)
		if other_account:
			frappe.throw(
				_(
					"Shopify Account {0} is already enabled. Only one account can be enabled at a time."
				).format(frappe.bold(other_account))
			)

	# --- Authentication ---

	def _validate_authentication_fields(self):
		"""Fail before any Shopify call: mandatory_depends_on is only checked after validate()."""
		if not self.is_enabled():
			return

		if self.authentication_method == AUTH_METHOD_OAUTH:
			if not self.client_id:
				frappe.throw(_("Client ID is required for OAuth 2.0 authentication"))
			if not self.get_password("client_secret", raise_exception=False):
				frappe.throw(_("Client Secret is required for OAuth 2.0 authentication"))
		else:
			if not self.get_password("password", raise_exception=False):
				frappe.throw(_("Password / Access Token is required for Static Token authentication"))
			if not self.shared_secret:
				frappe.throw(_("Shared secret / API Secret is required for Static Token authentication"))

	def _preserve_stored_oauth_token(self):
		"""A token stored by a background refresh is not in memory; _save_passwords would delete it."""
		ignored = self.flags.ignore_save_passwords
		if self.authentication_method != AUTH_METHOD_OAUTH or self.oauth_access_token or ignored is True:
			return

		self.flags.ignore_save_passwords = list({*(ignored or []), "oauth_access_token"})

	def _oauth_credentials_changed(self) -> bool:
		"""A token is issued for one app and one shop, so changing either invalidates it."""
		return any(self.has_value_changed(f) for f in ("client_id", "client_secret", "shopify_url"))

	def _get_or_generate_oauth_token(self) -> str:
		# persist=False: _save_passwords stores a new token only if this save succeeds
		if self._oauth_credentials_changed():
			return refresh_oauth_token(self, persist=False)
		return get_valid_access_token(self, persist=False)

	def _get_api_token(self) -> str | None:
		if self.authentication_method == AUTH_METHOD_OAUTH:
			return self._get_or_generate_oauth_token()
		return self.get_password("password", raise_exception=False)

	# --- Webhooks ---

	def _handle_webhooks(self):
		if not self.is_enabled():
			if self.webhooks:
				self._unregister_webhooks(self.shopify_url, self._get_api_token)
				self.webhooks = []
			return

		if self.webhooks and self._webhook_owner_changed():
			self._move_webhooks_to_new_owner()
		elif not self.webhooks:
			self._register_webhooks()

	def _move_webhooks_to_new_owner(self):
		"""Webhooks are signed with the registering app's secret, so they must move with the app.

		The new app is registered first: if Shopify rejects it, the save aborts and the
		old subscriptions keep delivering.
		"""
		previous = self.get_doc_before_save()
		token_field = (
			"oauth_access_token" if previous.authentication_method == AUTH_METHOD_OAUTH else "password"
		)
		previous_token = previous.get_password(token_field, raise_exception=False)

		previous_ids = [w.webhook_id for w in self.webhooks]

		self.webhooks = []
		self._register_webhooks()

		self._unregister_webhooks(previous.shopify_url, lambda: previous_token, webhook_ids=previous_ids)

	def _webhook_owner_changed(self) -> bool:
		if not self.get_doc_before_save():
			return False
		return any(self.has_value_changed(f) for f in ("authentication_method", "client_id", "shopify_url"))

	def _register_webhooks(self):
		try:
			new_webhooks = connection.register_webhooks(self.shopify_url, self._get_api_token())
		except requests.exceptions.RequestException as e:
			frappe.throw(
				_("Could not reach Shopify to register webhooks. Please try again. ({0})").format(str(e)),
				title=_("Shopify Connection Error"),
			)

		if not new_webhooks:
			msg = _("Failed to register webhooks with Shopify.") + "<br>"
			msg += _("Please check credentials and retry.") + " "
			msg += _("Disabling and re-enabling the integration might also help.")
			frappe.throw(msg)

		for webhook in new_webhooks:
			self.append("webhooks", {"webhook_id": webhook.id, "method": webhook.topic})

	def _unregister_webhooks(
		self, shopify_url: str, get_token: Callable[[], str | None], webhook_ids: list[str] | None = None
	):
		"""Runs only once this save commits: a remote deletion cannot be rolled back.

		Best effort: an unreachable Shopify must not block disabling or switching apps.
		"""

		def unregister():
			try:
				if not (token := get_token()):
					return
				if webhook_ids:
					connection.delete_webhooks(shopify_url, token, webhook_ids)
				else:
					connection.unregister_webhooks(shopify_url, token)
			except Exception as e:
				create_shopify_log(
					status="Warning",
					method="ecommerce_integrations.shopify.doctype.shopify_account.shopify_account._unregister_webhooks",
					shopify_account=self.name,
					message=_("Old Shopify webhooks could not be removed."),
					exception=str(e),
				)

		frappe.db.after_commit.add(unregister)

	# --- Warehouses ---

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
