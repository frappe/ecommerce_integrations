from collections import defaultdict
from copy import deepcopy

import frappe

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
from ecommerce_integrations.unicommerce.tests.test_client import UnicommerceClientTestSuite


class TestUnicommerceOrder(UnicommerceClientTestSuite):
	def assert_synced_order_fields(self, sales_order, order):
		customer_name = order["addresses"][0]["name"]
		self.assertIn(customer_name, sales_order.customer)
		self.assertEqual(sales_order.get(CHANNEL_ID_FIELD), order["channel"])
		self.assertEqual(sales_order.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(sales_order.get(ORDER_STATUS_FIELD), order["status"])

	def test_validate_item_list(self):
		expected_items_by_order = {
			"order-SO5905": {"MC-100", "TITANIUM_WATCH"},
			"order-SO5906": {"MC-100"},
			"order-SO5907": {"MC-100", "TITANIUM_WATCH"},
		}

		for order_file, expected_items in expected_items_by_order.items():
			with self.subTest(order=order_file):
				order = self.load_fixture(order_file)["saleOrderDTO"]
				self.assertEqual(expected_items, _sync_order_items(order, client=self.client))

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

		sales_order = create_order(order, client=self.client)

		self.assert_synced_order_fields(sales_order, order)

	def test_create_order_multiple_items(self):
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]

		sales_order = create_order(order, client=self.client)

		self.assert_synced_order_fields(sales_order, order)

		qty = sum(item.qty for item in sales_order.items)
		amount = sum(item.amount for item in sales_order.items)
		self.assertEqual(qty, 11)
		self.assertAlmostEqual(amount, 7028.0)
