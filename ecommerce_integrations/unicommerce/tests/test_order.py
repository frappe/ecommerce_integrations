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
	_validate_item_list,
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
		items_list = [{"MC-100", "TITANIUM_WATCH"}, {"MC-100",}, {"MC-100", "TITANIUM_WATCH"}]

		for order_file, items in zip(order_files, items_list):
			order = self.load_fixture(order_file)["saleOrderDTO"]
			self.assertEqual(items, _validate_item_list(order, client=self.client))

	def test_create_order(self):
		order = self.load_fixture("order-SO6008-order")

		so = create_order(order, client=self.client)

		customer_name = order["addresses"][0]["name"]
		self.assertTrue(customer_name in so.customer)
		self.assertEqual(so.get(CHANNEL_ID_FIELD), order["channel"])
		self.assertEqual(so.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(so.get(ORDER_STATUS_FIELD), order["status"])

	def test_get_line_items(self):
		pass

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
