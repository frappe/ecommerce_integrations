from unittest import skip

from frappe.test_runner import make_test_records

from ecommerce_integrations.unicommerce.order import _validate_item_list, create_order
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

	@skip
	def test_create_order(self):
		order = self.load_fixture("order-SO5907")["saleOrderDTO"]
		create_order(order)
