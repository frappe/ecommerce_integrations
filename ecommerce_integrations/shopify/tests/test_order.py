# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from ecommerce_integrations.shopify.order import get_order_items, update_taxes_with_shipping_lines
from ecommerce_integrations.utils.price_list import get_dummy_price_list
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

	def test_free_lines_keep_zero_rate_on_sales_order(self):
		# ERPNext falls back to the default price list for items missing from the dummy one;
		# a free line must not pick up that price
		price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
		for item_code in ("_Test Shopify Free Item", "_Test Shopify Shipping"):
			if not frappe.db.exists("Item", item_code):
				item = frappe.get_doc(
					doctype="Item",
					item_code=item_code,
					item_group="All Item Groups",
					stock_uom="Nos",
					is_stock_item=0,
				)
				# skip the upload to Shopify that other tests enable in Shopify Setting
				item.flags.from_integration = True
				item.insert()
			frappe.get_doc(
				doctype="Item Price", item_code=item_code, price_list=price_list, price_list_rate=15
			).insert()
		frappe.db.set_single_value("Selling Settings", "fallback_to_default_price_list", 1)
		if not frappe.db.exists("Customer", "_Test Shopify Customer"):
			frappe.get_doc(doctype="Customer", customer_name="_Test Shopify Customer").insert()

		setting = frappe._dict(add_shipping_as_item=1, shipping_item="_Test Shopify Shipping", warehouse=None)
		with patch(
			"ecommerce_integrations.shopify.order.get_item_code", return_value="_Test Shopify Free Item"
		):
			items = get_order_items(
				[
					{
						"name": "Free Tea",
						"quantity": 1,
						"price": "15.00",
						"product_exists": True,
						"discount_allocations": [{"amount": "15.00"}],
					}
				],
				setting,
				delivery_date=nowdate(),
				taxes_inclusive=False,
			)
		shipping_lines = [
			# free shipping rate: Shopify sends the price as the string "0.00"
			{"title": "Free Shipping", "price": "0.00", "tax_lines": []},
			# free shipping discount code allocated across a paid rate -> nets to 0
			{
				"title": "Standard",
				"price": "15.00",
				"discount_allocations": [{"amount": "15.00"}],
				"tax_lines": [],
			},
			# a paid rate must stay a normal, priced row
			{"title": "Express", "price": "25.00", "tax_lines": []},
		]
		update_taxes_with_shipping_lines([], shipping_lines, setting, items)

		so = frappe.get_doc(
			doctype="Sales Order",
			customer="_Test Shopify Customer",
			company=frappe.db.get_value("Company", {}, "name"),
			transaction_date=nowdate(),
			delivery_date=nowdate(),
			selling_price_list=get_dummy_price_list(),
			ignore_pricing_rule=1,
			items=items,
		)
		so.flags.ignore_mandatory = True
		so.save()

		self.assertEqual([item.rate for item in so.items], [0, 0, 0, 25])
		self.assertEqual(so.grand_total, 25)

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
