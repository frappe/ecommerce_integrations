from collections import defaultdict
from copy import deepcopy

import frappe
from frappe.test_runner import make_test_records

from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	ORDER_CODE_FIELD,
	ORDER_STATUS_FIELD,
)
from ecommerce_integrations.unicommerce.order import (
	_get_facility_code,
	_get_line_items,
	_sync_order_items,
	create_order,
)
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceOrder(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_records("Unicommerce Channel")

	def test_validate_item_list(self):
		order_files = ["order-SO5905", "order-SO5906", "order-SO5907"]
		items_list = [
			{"MC-100", "TITANIUM_WATCH"},
			{
				"MC-100",
			},
			{"MC-100", "TITANIUM_WATCH"},
		]

		for order_file, items in zip(order_files, items_list, strict=False):
			order = self.load_fixture(order_file)["saleOrderDTO"]
			self.assertEqual(items, _sync_order_items(order, client=self.client))

	def test_get_line_items(self):
		so_items = self.load_fixture("order-SO6008-order")["saleOrderItems"]
		items = _get_line_items(so_items)

		expected_item = {
			"item_code": "TITANIUM_WATCH",
			"rate": 312000.0,
			"qty": 1,
			"stock_uom": "Nos",
			"unicommerce_batch_code": None,
			"warehouse": "Stores - WP",
			"unicommerce_order_item_code": "TITANIUM_WATCH-0",
		}

		self.assertEqual(items[0], expected_item)

	def test_get_line_items_multiple(self):
		so_items = self.load_fixture("order-SO5906")["saleOrderDTO"]["saleOrderItems"]
		items = _get_line_items(so_items)

		item_to_qty = defaultdict(int)
		total_price = 0.0

		for item in items:
			item_to_qty[item["item_code"]] += item["qty"]
			total_price += item["rate"] * item["qty"]

		self.assertEqual(item_to_qty["MC-100"], 11)
		self.assertAlmostEqual(total_price, 7028.0)

	def test_get_taxes(self):
		pass

	def test_get_facility_code(self):
		line_items = self.load_fixture("order-SO6008-order")["saleOrderItems"]
		facility = _get_facility_code(line_items)

		self.assertEqual(facility, "Test-123")

		bad_line_item = deepcopy(line_items[0])
		bad_line_item["facilityCode"] = "grrr"
		line_items.append(bad_line_item)

		self.assertRaises(frappe.ValidationError, _get_facility_code, line_items)

	def test_create_order(self):
		order = self.load_fixture("order-SO6008-order")

		so = create_order(order, client=self.client)

		customer_name = order["addresses"][0]["name"]
		self.assertTrue(customer_name in so.customer)
		self.assertEqual(so.get(CHANNEL_ID_FIELD), order["channel"])
		self.assertEqual(so.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(so.get(ORDER_STATUS_FIELD), order["status"])

	def test_create_order_multiple_items(self):
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]

		so = create_order(order, client=self.client)

		customer_name = order["addresses"][0]["name"]
		self.assertTrue(customer_name in so.customer)
		self.assertEqual(so.get(CHANNEL_ID_FIELD), order["channel"])
		self.assertEqual(so.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(so.get(ORDER_STATUS_FIELD), order["status"])

		qty = sum(item.qty for item in so.items)
		amount = sum(item.amount for item in so.items)
		self.assertEqual(qty, 11)
		self.assertAlmostEqual(amount, 7028.0)
