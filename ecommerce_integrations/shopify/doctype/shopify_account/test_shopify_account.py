# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
import unittest
from frappe.test_runner import make_test_records

from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import ShopifyAccount


class TestShopifyAccount(unittest.TestCase):
	def setUp(self):
		"""Set up test data."""
		make_test_records("Company")
		
		# Create a test company if it doesn't exist
		if not frappe.db.exists("Company", "Test Company"):
			company = frappe.get_doc({
				"doctype": "Company",
				"company_name": "Test Company",
				"default_currency": "USD",
				"country": "United States"
			})
			company.insert()

	def tearDown(self):
		"""Clean up test data."""
		# Delete test Shopify accounts
		frappe.db.delete("Shopify Account", {"shop_domain": ["like", "%test%"]})

	def test_shop_domain_validation(self):
		"""Test shop domain format validation."""
		# Test valid domain
		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store",
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company"
		})
		account.validate()  # Should not raise any exception

		# Test invalid domain
		account.shop_domain = "invalid-domain.com"
		with self.assertRaises(frappe.ValidationError):
			account.validate()

		# Test domain with https prefix (should be auto-corrected)
		account.shop_domain = "https://test-store.myshopify.com"
		account.validate()
		self.assertEqual(account.shop_domain, "test-store.myshopify.com")

	def test_required_fields_when_enabled(self):
		"""Test required field validation when account is enabled."""
		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"enabled": 1,
			"account_title": "Test Store"
		})

		# Should fail validation due to missing required fields
		with self.assertRaises(frappe.ValidationError):
			account.validate()

		# Add required fields
		account.shop_domain = "test-store.myshopify.com"
		account.access_token = "test_token"
		account.shared_secret = "test_secret"
		account.company = "Test Company"
		
		# Should pass validation now
		account.validate()

	def test_company_consistency_validation(self):
		"""Test that all company-related fields belong to the same company."""
		# Create test cost center
		if not frappe.db.exists("Cost Center", "Test Cost Center - TC"):
			cost_center = frappe.get_doc({
				"doctype": "Cost Center",
				"cost_center_name": "Test Cost Center",
				"company": "Test Company",
				"parent_cost_center": "Test Company - TC"
			})
			cost_center.insert()

		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store",
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company",
			"cost_center": "Test Cost Center - TC"
		})
		
		# Should pass validation
		account.validate()

	def test_warehouse_mapping_validation(self):
		"""Test warehouse mapping validation."""
		# Create test warehouse
		if not frappe.db.exists("Warehouse", "Test Warehouse - TC"):
			warehouse = frappe.get_doc({
				"doctype": "Warehouse",
				"warehouse_name": "Test Warehouse",
				"company": "Test Company"
			})
			warehouse.insert()

		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store",
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company"
		})

		# Add warehouse mapping
		account.append("warehouse_mappings", {
			"shopify_location_id": "12345",
			"shopify_location_name": "Test Location",
			"erpnext_warehouse": "Test Warehouse - TC"
		})

		# Should pass validation
		account.validate()

	def test_duplicate_shop_domain(self):
		"""Test that shop domains must be unique."""
		# Create first account
		account1 = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store 1",
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company"
		})
		account1.insert()

		# Try to create second account with same domain
		account2 = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store 2", 
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company"
		})

		# Should fail due to unique constraint
		with self.assertRaises(frappe.DuplicateEntryError):
			account2.insert()

	def test_static_methods(self):
		"""Test static utility methods."""
		# Create a test account
		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store",
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company",
			"enabled": 1
		})
		account.insert()

		# Test get_account_by_domain
		found_account = ShopifyAccount.get_account_by_domain("test-store.myshopify.com")
		self.assertEqual(found_account.name, account.name)

		# Test get_enabled_accounts
		enabled_accounts = ShopifyAccount.get_enabled_accounts()
		self.assertTrue(any(acc.name == account.name for acc in enabled_accounts))

	def test_sync_status_update(self):
		"""Test sync status update functionality."""
		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store",
			"shop_domain": "test-store.myshopify.com", 
			"company": "Test Company"
		})
		account.insert()

		# Test status update
		account.update_sync_status("Success")
		self.assertEqual(account.last_sync_status, "Success")
		self.assertIsNotNone(account.last_sync_at)

		# Test invalid status
		with self.assertRaises(frappe.ValidationError):
			account.update_sync_status("InvalidStatus")

	def test_helper_methods(self):
		"""Test helper methods."""
		account = frappe.get_doc({
			"doctype": "Shopify Account",
			"account_title": "Test Store",
			"shop_domain": "test-store.myshopify.com",
			"company": "Test Company",
			"enabled": 1
		})

		# Test is_enabled
		self.assertTrue(account.is_enabled())

		account.enabled = 0
		self.assertFalse(account.is_enabled())

		# Test get_shop_url
		self.assertEqual(account.get_shop_url(), "https://test-store.myshopify.com")
