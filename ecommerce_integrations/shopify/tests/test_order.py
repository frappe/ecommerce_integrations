# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.shopify.order import get_order_items
from ecommerce_integrations.utils.taxation import ITEM_WISE_TAX_KEY, set_item_wise_tax_details


class TestOrder(IntegrationTestCase):
	def test_sync_with_variants(self):
		pass

	@patch("ecommerce_integrations.shopify.order.get_item_code", return_value="_Test Item")
	def test_fully_discounted_line_is_marked_free(self, _):
		# 100% discount code allocated across the line -> nets to 0
		line_items = [
			{
				"name": "Free Tea",
				"quantity": 2,
				"price": "13.95",
				"product_exists": True,
				"discount_allocations": [{"amount": "27.90"}],
			},
			# partially discounted line must stay a normal, priced row
			{
				"name": "Discounted Tea",
				"quantity": 1,
				"price": "10.00",
				"product_exists": True,
				"discount_allocations": [{"amount": "1.00"}],
			},
		]

		items = get_order_items(
			line_items, frappe._dict(warehouse="_Test Warehouse"), delivery_date=None, taxes_inclusive=False
		)

		self.assertEqual(items[0]["rate"], 0)
		self.assertEqual(items[0]["is_free_item"], 1)
		self.assertEqual(items[1]["rate"], 9.0)
		self.assertEqual(items[1]["is_free_item"], 0)

	def test_set_item_wise_tax_details_builds_item_wise_details(self):
		# dont_recompute rows must seed the item-wise breakup, else IC rejects the order.
		if not frappe.get_meta("Sales Order").get_field("item_wise_tax_details"):
			self.skipTest("ERPNext without item_wise_tax_details table")

		so = frappe.new_doc("Sales Order")
		so.append("items", {"item_code": "SHOPIFY-A", "qty": 1, "rate": 100})
		so.append("items", {"item_code": "SHOPIFY-B", "qty": 2, "rate": 50})
		so.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": "CGST",
				"tax_amount": 18,
				"dont_recompute_tax": 1,
				ITEM_WISE_TAX_KEY: {"SHOPIFY-A": [9, 9], "SHOPIFY-B": [9, 9]},
			},
		)

		set_item_wise_tax_details(so)

		rows = so.get("_item_wise_tax_details")
		self.assertEqual(len(rows), 2)
		by_item = {row.item.item_code: row for row in rows}
		self.assertEqual(by_item["SHOPIFY-A"].rate, 9)
		self.assertEqual(by_item["SHOPIFY-A"].amount, 9)
		self.assertEqual(by_item["SHOPIFY-A"].taxable_amount, 100)
		self.assertEqual(by_item["SHOPIFY-B"].taxable_amount, 100)

	def test_set_item_wise_tax_details_skips_rows_without_item_wise_tax(self):
		# Rows without per-item data (e.g. shipping without an item) produce no rows.
		if not frappe.get_meta("Sales Order").get_field("item_wise_tax_details"):
			self.skipTest("ERPNext without item_wise_tax_details table")

		so = frappe.new_doc("Sales Order")
		so.append("items", {"item_code": "SHOPIFY-A", "qty": 1, "rate": 100})
		so.append(
			"taxes",
			{"charge_type": "Actual", "account_head": "Shipping", "tax_amount": 50, "dont_recompute_tax": 1},
		)

		set_item_wise_tax_details(so)

		self.assertFalse(so.get("_item_wise_tax_details"))
