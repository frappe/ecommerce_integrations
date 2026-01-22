import frappe
from frappe.tests import IntegrationTestCase


class EcommerceTestSuite(IntegrationTestCase):
	"""Base test class for ecommerce_integrations
	
	Provides comprehensive test data setup for all ecommerce modules.
	Extends IntegrationTestCase which handles database transactions properly.
	
	Usage:
		from ecommerce_integrations.tests.utils import EcommerceTestSuite
		
		class TestMyModule(EcommerceTestSuite):
			def test_something(self):
				# All test data is automatically available
				pass
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.create_test_data()

	@classmethod
	def create_test_data(cls):
		"""Create all necessary test data"""
		cls.create_warehouse_types()
		cls.create_customer_groups()
		cls.create_test_company()
		cls.create_test_customer_group()
		cls.create_test_customer()
		cls.create_test_warehouses()
		cls.create_wp_warehouses()
		cls.create_test_items()
		cls.create_test_bank_account()
		cls.create_tax_account()

	@classmethod
	def create_warehouse_types(cls):
		"""Create warehouse types if they don't exist"""
		warehouse_types = ["Transit", "Regular"]
		
		for wh_type in warehouse_types:
			if not frappe.db.exists("Warehouse Type", wh_type):
				try:
					frappe.get_doc(
						{
							"doctype": "Warehouse Type",
							"name": wh_type,
						}
					).insert(ignore_if_duplicate=True)
				except Exception:
					pass

	@classmethod
	def create_customer_groups(cls):
		"""Create customer groups if they don't exist"""
		customer_groups = ["Individual", "Commercial", "_Test Customer Group 1"]
		
		for group_name in customer_groups:
			if not frappe.db.exists("Customer Group", group_name):
				try:
					frappe.get_doc(
						{
							"doctype": "Customer Group",
							"customer_group_name": group_name,
							"parent_customer_group": "All Customer Groups",
						}
					).insert(ignore_if_duplicate=True)
				except Exception:
					pass

	@classmethod
	def create_test_company(cls):
		"""Create _Test Company if it doesn't exist"""
		if not frappe.db.exists("Company", "_Test Company"):
			try:
				frappe.get_doc(
					{
						"doctype": "Company",
						"company_name": "_Test Company",
						"country": "India",
						"default_currency": "INR",
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass

	@classmethod
	def create_test_customer_group(cls):
		"""Create _Test Customer Group 1 if it doesn't exist"""
		if not frappe.db.exists("Customer Group", "_Test Customer Group 1"):
			try:
				frappe.get_doc(
					{
						"doctype": "Customer Group",
						"customer_group_name": "_Test Customer Group 1",
						"parent_customer_group": "All Customer Groups",
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass

	@classmethod
	def create_test_customer(cls):
		"""Create _Test Customer if it doesn't exist"""
		if not frappe.db.exists("Customer", "_Test Customer"):
			try:
				frappe.get_doc(
					{
						"doctype": "Customer",
						"customer_name": "_Test Customer",
						"customer_type": "Individual",
						"customer_group": "_Test Customer Group 1",
						"territory": "All Territories",
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass

	@classmethod
	def create_test_warehouses(cls):
		"""Create test warehouses if they don't exist"""
		warehouses = [
			"_Test Warehouse - _TC",
			"_Test Warehouse 1 - _TC",
			"_Test Warehouse 2 - _TC",
		]

		for warehouse_name in warehouses:
			if not frappe.db.exists("Warehouse", warehouse_name):
				try:
					frappe.get_doc(
						{
							"doctype": "Warehouse",
							"warehouse_name": warehouse_name,
							"company": "_Test Company",
						}
					).insert(ignore_if_duplicate=True)
				except Exception:
					pass

	@classmethod
	def create_wp_warehouses(cls):
		"""Create Wind Power LLC warehouses if they don't exist"""
		warehouses = [
			"Stores - WP",
			"Work In Progress - WP",
		]

		for warehouse_name in warehouses:
			if not frappe.db.exists("Warehouse", warehouse_name):
				try:
					frappe.get_doc(
						{
							"doctype": "Warehouse",
							"warehouse_name": warehouse_name,
							"company": "Wind Power LLC",
						}
					).insert(ignore_if_duplicate=True)
				except Exception:
					pass

	@classmethod
	def create_test_items(cls):
		"""Create test items if they don't exist"""
		test_items = [
			{
				"item_code": "_Test Item",
				"item_name": "_Test Item",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 1,
			},
			{
				"item_code": "_Test Item 2",
				"item_name": "_Test Item 2",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 1,
			},
		]

		for item_data in test_items:
			if not frappe.db.exists("Item", item_data["item_code"]):
				try:
					frappe.get_doc(
						{
							"doctype": "Item",
							**item_data,
						}
					).insert(ignore_if_duplicate=True)
				except Exception:
					pass

	@classmethod
	def create_test_bank_account(cls):
		"""Create _Test Bank - _TC account if it doesn't exist"""
		if not frappe.db.exists("Account", "_Test Bank - _TC"):
			try:
				# Get the cash/bank account group
				parent = frappe.db.get_value(
					"Account",
					{"company": "_Test Company", "account_type": "Bank", "is_group": 1},
				)
				if not parent:
					parent = "Bank Accounts - _TC"
				
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": "_Test Bank",
						"account_type": "Bank",
						"company": "_Test Company",
						"parent_account": parent,
						"is_group": 0,
						"root_type": "Asset",
						"report_type": "Balance Sheet",
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass

	@classmethod
	def create_tax_account(cls):
		"""Create tax account for Wind Power LLC"""
		company = "Wind Power LLC"
		account_name = "Output Tax GST"

		parent = (
			frappe.db.get_value("Account", {"company": company, "account_type": "Tax", "is_group": 1})
			or "Duties and Taxes - WP"
		)

		# Check if account already exists
		if not frappe.db.exists("Account", {"account_name": account_name, "company": company}):
			try:
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": account_name,
						"is_group": 0,
						"company": company,
						"root_type": "Liability",
						"report_type": "Balance Sheet",
						"account_currency": "INR",
						"parent_account": parent,
						"account_type": "Tax",
						"tax_rate": 18,
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass
