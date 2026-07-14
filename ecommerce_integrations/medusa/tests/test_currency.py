# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.utils import flt

from ecommerce_integrations.medusa.constants import ORDER_ID_FIELD
from ecommerce_integrations.medusa.invoice import prepare_sales_invoice
from ecommerce_integrations.medusa.order import sync_sales_order

from .utils import TestCase

ORDER_ID = "order_01HORDER00000000000000001"


class TestMultiCurrency(TestCase):
	def setUp(self):
		super().setUp()
		# the test company is INR; seed a deterministic USD->INR rate for the foreign invoice
		self.company_currency = frappe.get_cached_value("Company", "_Test Company", "default_currency")
		if not frappe.db.exists(
			"Currency Exchange", {"from_currency": "USD", "to_currency": self.company_currency}
		):
			frappe.get_doc(
				{
					"doctype": "Currency Exchange",
					"date": "2024-01-01",
					"from_currency": "USD",
					"to_currency": self.company_currency,
					"exchange_rate": 80,
				}
			).insert(ignore_permissions=True)

	def test_usd_order_invoices_against_a_currency_receivable(self):
		if self.company_currency == "USD":
			self.skipTest("test company is USD; nothing foreign to exercise")

		order = self.load_fixture("order_usd")
		sync_sales_order(order)

		so = frappe.get_doc("Sales Order", {ORDER_ID_FIELD: ORDER_ID})
		self.assertEqual(so.currency, "USD")
		self.assertEqual(flt(so.conversion_rate), 80)

		prepare_sales_invoice(order)

		si_name = frappe.db.get_value(
			"Sales Invoice", {ORDER_ID_FIELD: ORDER_ID, "is_return": 0, "docstatus": 1}
		)
		self.assertTrue(si_name)
		si = frappe.get_doc("Sales Invoice", si_name)
		self.assertEqual(si.currency, "USD")
		# debit_to is a USD receivable (auto-created), not the company-currency default
		self.assertEqual(frappe.db.get_value("Account", si.debit_to, "account_currency"), "USD")
		# base total is converted to company currency via the rate
		self.assertEqual(flt(si.base_grand_total, 2), flt(si.grand_total * 80, 2))
		# payment recorded
		self.assertTrue(frappe.db.exists("Payment Entry", {"reference_no": si_name}))
