import json
from unittest.mock import patch

import frappe
import responses

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.tests.utils import TestCase


class TestCaseApiClient(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		with patch(
			"ecommerce_integrations.unicommerce.doctype.unicommerce_settings.unicommerce_settings.UnicommerceSettings.renew_tokens"
		):
			cls.client = UnicommerceAPIClient()

	def setUp(self):
		self.responses = responses.RequestsMock()
		self.responses.start()

		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json=self.load_fixture("simple_item"),
			match=[responses.json_params_matcher({"skuCode": "TITANIUM_WATCH"})],
		)

		def sales_order_mock(request):
			payload = json.loads(request.body)
			resp_body = self.load_fixture(f"order-{payload['code']}")
			headers = {}
			return (200, headers, json.dumps(resp_body))

		self.responses.add_callback(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/saleorder/get",
			callback=sales_order_mock,
			content_type="application/json",
		)

		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/saleOrder/search",
			status=200,
			json=self.load_fixture("so_search_results"),
		)

		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json=self.load_fixture("product-MC-100"),
			match=[responses.json_params_matcher({"skuCode": "MC-100"})],
		)

		self.addCleanup(self.responses.stop)
		self.addCleanup(self.responses.reset)


class TestUnicommerceClient(TestCaseApiClient):
	def test_authorization_headers(self):
		"""requirement: client inserts bearer token in headers"""
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json={"status": "fail"},
			match=[responses.json_params_matcher({"skuCode": "sku"})],
		)

		ret, _ = self.client.request(endpoint="get_item", body={"skuCode": "sku"})
		self.assertEqual(ret["status"], "fail")

		req_headers = self.responses.calls[0].request.headers
		self.assertEqual(req_headers["Authorization"], "Bearer AUTH_TOKEN")

	def test_get_item(self):
		"""requirement: When querying correct item, item is returned as _dict"""

		item_data = self.client.get_unicommerce_item("TITANIUM_WATCH")

		self.assertIsNotNone(item_data)
		self.assertTrue(item_data.successful)

		# TODO: recursive _dict
		self.assertEqual(item_data.itemTypeDTO["id"], 129851)
		self.assertEqual(item_data.itemTypeDTO["skuCode"], "TITANIUM_WATCH")
		self.assertEqual(item_data.itemTypeDTO["weight"], 1000)

	def test_get_missing_item(self):
		"""requirement: When querying missing item, `None` is returned and error log is crated"""
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json=self.load_fixture("missing_item"),
			match=[responses.json_params_matcher({"skuCode": "MISSING"})],
		)

		item_data = self.client.get_unicommerce_item("MISSING")
		self.assertIsNone(item_data)

		log = frappe.get_last_doc("Ecommerce Integration Log", filters={"integration": "unicommerce"})
		self.assertTrue("MISSING" in log.response_data, "Logging for missing item not working")

	def test_get_sales_order(self):
		order_data = self.client.get_sales_order("SO5841")

		self.assertEqual(order_data["code"], "SO5841")
		self.assertEqual(order_data["displayOrderCode"], "SINV-00042")

	def test_create_update_item(self):
		item_dict = {"test_dict": True}
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/createOrEdit",
			status=200,
			json={"successful": True},
			match=[responses.json_params_matcher({"itemType": item_dict})],
		)

		response, _ = self.client.create_update_item(item_dict)
		self.assertTrue(response["successful"])

	def test_bulk_inventory_sync(self):

		expected_body = {
			"inventoryAdjustments": [
				{
					"itemSKU": "A",
					"quantity": 1,
					"shelfCode": "DEFAULT",
					"inventoryType": "GOOD_INVENTORY",
					"adjustmentType": "REPLACE",
					"facilityCode": "42",
				},
				{
					"itemSKU": "B",
					"quantity": 2,
					"shelfCode": "DEFAULT",
					"inventoryType": "GOOD_INVENTORY",
					"adjustmentType": "REPLACE",
					"facilityCode": "42",
				},
			]
		}
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/inventory/adjust/bulk",
			status=200,
			json=self.load_fixture("bulk_inventory_response"),
			match=[responses.json_params_matcher(expected_body)],
		)

		inventory_map = {"A": 1, "B": 2}
		response, status = self.client.bulk_inventory_update("42", inventory_map)

		req_headers = self.responses.calls[0].request.headers
		self.assertEqual(req_headers["Facility"], "42")

		self.assertTrue(status)
		self.assertDictEqual(response, {k: True for k in inventory_map})