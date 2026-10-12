# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.shopify.order import get_order_items
from ecommerce_integrations.utils.taxation import (
	ITEM_WISE_TAX_KEY,
	copy_item_wise_tax_details,
	set_item_wise_tax_details,
)


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


class TestCopyItemWiseTaxDetails(unittest.TestCase):
	"""A doc mapped from another transaction keeps each tax row's own breakup."""

	@staticmethod
	def _source():
		"""One tax row per line item, all on the same account (Shopify without tax consolidation)."""
		return frappe._dict(
			taxes=[
				frappe._dict(name="T1", account_head="GST - TC"),
				frappe._dict(name="T2", account_head="GST - TC"),
			],
			item_wise_tax_details=[
				frappe._dict(tax_row="T1", item_row="SOI-A", rate=18.0, amount=18.0, taxable_amount=100.0),
				frappe._dict(tax_row="T2", item_row="SOI-B", rate=18.0, amount=36.0, taxable_amount=200.0),
			],
		)

	@staticmethod
	def _target(items):
		tax_rows = [
			frappe._dict(name="DT1", account_head="GST - TC"),
			frappe._dict(name="DT2", account_head="GST - TC"),
		]
		target = MagicMock()
		target.get.side_effect = {"items": items, "taxes": tax_rows}.get
		return target, tax_rows

	def test_copies_each_rows_breakup_to_its_own_tax_row(self):
		target, (tax_a, tax_b) = self._target(
			[frappe._dict(so_detail="SOI-A"), frappe._dict(so_detail="SOI-B")]
		)

		with patch("frappe.get_doc", return_value=self._source()):
			copy_item_wise_tax_details(target, "SO-1")

		rows = target._item_wise_tax_details
		self.assertEqual([row.tax for row in rows], [tax_a, tax_b])
		self.assertAlmostEqual(rows[0].amount, 18.0)
		self.assertAlmostEqual(rows[1].amount, 36.0)

	def test_row_of_an_absent_item_keeps_its_queue_slot(self):
		"""Shipping only item B must not move B's breakup onto item A's tax row."""
		target, (tax_a, tax_b) = self._target([frappe._dict(so_detail="SOI-B")])

		with patch("frappe.get_doc", return_value=self._source()):
			copy_item_wise_tax_details(target, "SO-1")

		rows = target._item_wise_tax_details
		self.assertEqual(len(rows), 1)
		self.assertIs(rows[0].tax, tax_b)
		self.assertAlmostEqual(rows[0].amount, 36.0)
