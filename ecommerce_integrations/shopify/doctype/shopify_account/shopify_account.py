# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address
from shopify.collection import PaginatedIterator
from shopify.resources import Location

from ecommerce_integrations.shopify import connection


class ShopifyAccount(Document):
	def validate(self):
		"""Validate Shopify Account settings before saving."""
		self.validate_shop_domain()
		self.validate_required_when_enabled()
		self.validate_company_consistency()
		self.validate_warehouse_mappings()
		self.validate_tax_mappings()
		self.validate_feature_dependencies()

	def validate_shop_domain(self):
		"""Validate shop domain format."""
		if self.shop_domain:
			# Remove https:// or http:// if present
			self.shop_domain = self.shop_domain.replace("https://", "").replace("http://", "")
			
			# Ensure it ends with .myshopify.com
			if not self.shop_domain.endswith(".myshopify.com"):
				frappe.throw(_("Shop Domain must be a valid Shopify domain ending with '.myshopify.com'"))

	def validate_required_when_enabled(self):
		"""Validate required fields when account is enabled."""
		if self.enabled:
			required_fields = ["shop_domain", "access_token", "shared_secret", "company"]
			for field in required_fields:
				if not self.get(field):
					frappe.throw(_("{0} is required when account is enabled").format(self.meta.get_label(field)))

	def validate_company_consistency(self):
		"""Validate that all company-related fields belong to the same company."""
		if not self.company:
			return

		# Validate cost center belongs to company
		if self.cost_center:
			cost_center_company = frappe.db.get_value("Cost Center", self.cost_center, "company")
			if cost_center_company != self.company:
				frappe.throw(_("Cost Center {0} does not belong to Company {1}").format(
					self.cost_center, self.company))

		# Validate default customer belongs to company
		if self.default_customer:
			customer_company = frappe.db.get_value("Customer", self.default_customer, "company")
			# Customer might not have a company set, so only validate if it's set
			if customer_company and customer_company != self.company:
				frappe.throw(_("Default Customer {0} does not belong to Company {1}").format(
					self.default_customer, self.company))

	def validate_warehouse_mappings(self):
		"""Validate warehouse mappings."""
		if not self.warehouse_mappings:
			return

		seen_locations = set()
		default_count = 0

		for mapping in self.warehouse_mappings:
			# Check for duplicate Shopify location IDs
			if mapping.shopify_location_id in seen_locations:
				frappe.throw(_("Duplicate Shopify Location ID: {0}").format(mapping.shopify_location_id))
			seen_locations.add(mapping.shopify_location_id)

			# Validate warehouse belongs to the same company
			if mapping.erpnext_warehouse:
				warehouse_company = frappe.db.get_value("Warehouse", mapping.erpnext_warehouse, "company")
				if warehouse_company != self.company:
					frappe.throw(_("Warehouse {0} does not belong to Company {1}").format(
						mapping.erpnext_warehouse, self.company))

			# Count default warehouses (if we add is_default field later)
			if hasattr(mapping, 'is_default') and mapping.is_default:
				default_count += 1

		# Ensure only one default warehouse (if we add is_default field later)
		if default_count > 1:
			frappe.throw(_("Only one warehouse can be marked as default"))

	def validate_tax_mappings(self):
		"""Validate tax mappings."""
		if not self.tax_mappings:
			return

		seen_tax_keys = set()

		for mapping in self.tax_mappings:
			# Check for duplicate tax keys
			if mapping.shopify_tax in seen_tax_keys:
				frappe.throw(_("Duplicate Shopify Tax/Shipping Title: {0}").format(mapping.shopify_tax))
			seen_tax_keys.add(mapping.shopify_tax)

			# Validate tax account belongs to the same company
			if mapping.tax_account:
				account_company = frappe.db.get_value("Account", mapping.tax_account, "company")
				if account_company != self.company:
					frappe.throw(_("Tax Account {0} does not belong to Company {1}").format(
						mapping.tax_account, self.company))

	def validate_feature_dependencies(self):
		"""Validate feature toggle dependencies."""
		warnings = []

		# Warn if sync features are enabled but cost center is missing
		if (self.sync_sales_invoice or self.sync_delivery_note) and not self.cost_center:
			warnings.append(_("Cost Center is recommended when Sales Invoice or Delivery Note sync is enabled"))

		# Warn if customer creation is disabled but no default customer is set
		if not self.create_customers and not self.default_customer:
			warnings.append(_("Default Customer is required when automatic customer creation is disabled"))

		# Show warnings as messages (non-blocking)
		for warning in warnings:
			frappe.msgprint(warning, indicator="orange", alert=True)

	def is_enabled(self) -> bool:
		"""Check if this Shopify account is enabled."""
		return bool(self.enabled)

	def get_shop_url(self) -> str:
		"""Get the full shop URL with https prefix."""
		if self.shop_domain:
			return f"https://{self.shop_domain}"
		return ""

	def get_access_token(self) -> str:
		"""Get the decrypted access token."""
		return self.get_password("access_token")

	def get_shared_secret(self) -> str:
		"""Get the decrypted shared secret."""
		return self.get_password("shared_secret")

	@staticmethod
	def get_account_by_domain(shop_domain: str):
		"""Get Shopify Account by shop domain."""
		return frappe.get_doc("Shopify Account", {"shop_domain": shop_domain, "enabled": 1})

	@staticmethod
	def get_enabled_accounts():
		"""Get all enabled Shopify accounts."""
		return frappe.get_all("Shopify Account", 
			filters={"enabled": 1}, 
			fields=["name", "account_title", "shop_domain", "company"])

	def update_sync_status(self, status: str, sync_time=None):
		"""Update the last sync status and time."""
		if status not in ["Idle", "Success", "Warning", "Error"]:
			frappe.throw(_("Invalid sync status: {0}").format(status))
		
		self.last_sync_status = status
		if sync_time:
			self.last_sync_at = sync_time
		else:
			self.last_sync_at = frappe.utils.now()
		
		# Save without calling validate again to avoid recursion
		self.db_set("last_sync_status", self.last_sync_status)
		self.db_set("last_sync_at", self.last_sync_at)

	@frappe.whitelist()
	def fetch_shopify_locations(self):
		"""Fetch locations from Shopify and add them to warehouse mapping table."""
		if not self.enabled:
			frappe.throw(_("Account must be enabled to fetch locations"))
		
		if not self.get_access_token():
			frappe.throw(_("Access token is required to fetch locations"))

		# Clear existing mappings
		self.warehouse_mappings = []
		
		# Pass account parameter explicitly
		self._fetch_locations_with_session(account=self)
		
		frappe.msgprint(_("Successfully fetched {0} locations from Shopify").format(len(self.warehouse_mappings)))

	@connection.temp_shopify_session
	def _fetch_locations_with_session(self, account=None):
		"""Internal method to fetch locations with session context."""
		try:
			for locations in PaginatedIterator(Location.find()):
				for location in locations:
					self.append("warehouse_mappings", {
						"shopify_location_id": location.id,
						"shopify_location_name": location.name
					})
		except Exception as e:
			# Import the logging function
			from ecommerce_integrations.shopify.utils import create_shopify_log
			
			# Create error log entry
			create_shopify_log(
				status="Error",
				method="fetch_shopify_locations",
				message=f"Failed to fetch Shopify locations: {str(e)}",
				exception=e,
				account=account
			)
			
			# Then throw the exception for user feedback
			frappe.throw(_("Failed to fetch Shopify locations: {0}").format(str(e)))
