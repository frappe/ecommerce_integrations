# Copyright (c) 2025, Frappe and Contributors
# See LICENSE

import frappe
from erpnext.tests.utils import ERPNextTestSuite


class EcommerceBootstrapTestData:
	def __init__(self):
		self.make_company()
		self.make_tax_account()
		self.make_currency_exchange()
		self.update_global_defaults()
		self.update_stock_settings()
		self.update_selling_settings()

		frappe.db.commit()  # nosemgrep

	def make_records(self, key, records):
		doctype = records[0].get("doctype")

		def get_filters(record):
			filters = {}
			for x in key:
				filters[x] = record.get(x)
			return filters

		for x in records:
			filters = get_filters(x)
			if not frappe.db.exists(doctype, filters):
				frappe.get_doc(x).insert()

	def make_company(self):
		# erpnext ships "Wind Power LLC" as a USD global test record, but the
		# ecommerce test data (channels, accounts, orders) assumes INR. Recreate
		# it as an INR company so downstream sales documents resolve correctly.
		existing_currency = frappe.db.get_value("Company", "Wind Power LLC", "default_currency")
		if existing_currency == "INR":
			return
		if existing_currency:
			frappe.delete_doc("Company", "Wind Power LLC", force=True, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Wind Power LLC",
				"abbr": "WP",
				"country": "India",
				"default_currency": "INR",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)

	def make_tax_account(self):
		parent = (
			frappe.db.get_value(
				"Account", {"company": "Wind Power LLC", "account_type": "Tax", "is_group": 1}
			)
			or "Duties and Taxes - WP"
		)

		records = [
			{
				"doctype": "Account",
				"account_name": "Output Tax GST",
				"parent_account": parent,
				"company": "Wind Power LLC",
				"is_group": 0,
				"account_type": "Tax",
				"tax_rate": 18,
			}
		]
		self.make_records(["account_name", "company"], records)

	def update_global_defaults(self):
		global_defaults = frappe.get_doc("Global Defaults")
		global_defaults.default_company = "Wind Power LLC"
		global_defaults.save()

	def update_stock_settings(self):
		stock_settings = frappe.get_doc("Stock Settings")
		stock_settings.default_warehouse = frappe.db.get_value(
			"Warehouse", {"warehouse_name": "Stores", "company": "Wind Power LLC"}
		)
		stock_settings.auto_insert_price_list_rate_if_missing = 0
		stock_settings.save()

	def update_selling_settings(self):
		# Marketplace orders can contain the same item across multiple rows.
		selling_settings = frappe.get_doc("Selling Settings")
		selling_settings.allow_multiple_items = 1
		selling_settings.save()

	def make_currency_exchange(self):
		# The test company uses INR while the default price list is in USD;
		# provide exchange rates so sales documents can resolve the conversion.
		for from_currency, to_currency, rate in (("USD", "INR", 80.0), ("INR", "USD", 0.0125)):
			if frappe.db.exists(
				"Currency Exchange", {"from_currency": from_currency, "to_currency": to_currency}
			):
				continue
			frappe.get_doc(
				{
					"doctype": "Currency Exchange",
					"date": "2000-01-01",
					"from_currency": from_currency,
					"to_currency": to_currency,
					"exchange_rate": rate,
					"for_selling": 1,
					"for_buying": 1,
				}
			).insert(ignore_permissions=True)


EcommerceBootstrapTestData()


class EcommerceTestSuite(ERPNextTestSuite):
	pass
