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


class TestUnicommerceClient(TestCaseApiClient):
	@responses.activate
	def test_authorization_headers(self):
		"""requirement: client inserts bearer token in headers"""
		responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json={"status": "fail"},
			match=[responses.json_params_matcher({"skuCode": "sku"})],
		)

		ret, _ = self.client.request(endpoint="get_item", body={"skuCode": "sku"})
		self.assertEqual(ret["status"], "fail")

		req_headers = responses.calls[0].request.headers
		self.assertEqual(req_headers["Authorization"], "Bearer AUTH_TOKEN")

	@responses.activate
	def test_get_item(self):
		"""requirement: When querying correct item, item is returned as _dict"""
		responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json=self.load_fixture("simple_item"),
			match=[responses.json_params_matcher({"skuCode": "TITANIUM_WATCH"})],
		)

		item_data = self.client.get_unicommerce_item("TITANIUM_WATCH")

		self.assertIsNotNone(item_data)
		self.assertTrue(item_data.successful)

		# TODO: recursive _dict
		self.assertEqual(item_data.itemTypeDTO["id"], 129851)
		self.assertEqual(item_data.itemTypeDTO["skuCode"], "TITANIUM_WATCH")
		self.assertEqual(item_data.itemTypeDTO["weight"], 1000)

	@responses.activate
	def test_get_missing_item(self):
		"""requirement: When querying missing item, `None` is returned and error log is crated"""
		responses.add(
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

	@responses.activate
	def test_get_sales_order(self):
		responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/saleorder/get",
			status=200,
			json=self.load_fixture("simple_order"),
			match=[responses.json_params_matcher({"code": "SO5841"})],
		)

		order_data = self.client.get_sales_order("SO5841")

		self.assertEqual(order_data["saleOrderDTO"]["code"], "SO5841")
		self.assertEqual(order_data["saleOrderDTO"]["displayOrderCode"], "SINV-00042")

	@responses.activate
	def test_search_sales_order(self):
		pass

	@responses.activate
	def test_create_update_item(self):
		item_dict = {"test_dict": True}
		responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/createOrEdit",
			status=200,
			json={"successful": True},
			match=[responses.json_params_matcher({"itemType": item_dict})],
		)

		response, _ = self.client.create_update_item(item_dict)
		self.assertTrue(response["successful"])

	@responses.activate
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
		responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/inventory/adjust/bulk",
			status=200,
			json=self.load_fixture("bulk_inventory_response"),
			match=[responses.json_params_matcher(expected_body)],
		)

		inventory_map = {"A": 1, "B": 2}
		response, status = self.client.bulk_inventory_update("42", inventory_map)

		req_headers = responses.calls[0].request.headers
		self.assertEqual(req_headers["Facility"], "42")

		self.assertTrue(status)
		self.assertDictEqual(response, {k: True for k in inventory_map})
