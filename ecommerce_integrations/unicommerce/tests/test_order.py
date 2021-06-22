import json

import frappe
import responses
from frappe.test_runner import make_test_records

from ecommerce_integrations.unicommerce.constants import MODULE_NAME
from ecommerce_integrations.unicommerce.order import fetch_new_orders
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceProduct(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_records("Unicommerce Channel")

	@responses.activate
	def test_fetch_new_order(self):
		def sales_order_mock(request):
			payload = json.loads(request.body)
			resp_body = self.load_fixture(f"order-{payload['code']}")
			headers = {}
			return (200, headers, json.dumps(resp_body))

		responses.add_callback(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/saleorder/get",
			callback=sales_order_mock,
			content_type="application/json",
		)

		responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/saleOrder/search",
			status=200,
			json=self.load_fixture("so_search_results"),
		)
		fetch_new_orders(client=self.client, force=True)
