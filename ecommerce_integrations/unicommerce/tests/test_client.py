import base64
import json
from unittest.mock import patch

import frappe
import responses
from responses.matchers import query_param_matcher

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.tests.utils import TestCase


class TestCaseApiClient(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.client = UnicommerceAPIClient("https://demostaging.unicommerce.com", "AUTH_TOKEN")

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

	def assert_last_request_headers(self, header, value):
		req_headers = self.responses.calls[0].request.headers
		self.assertEqual(req_headers[header], value)


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

		ret, _ = self.client.request(
			endpoint="/services/rest/v1/catalog/itemType/get", body={"skuCode": "sku"}
		)
		self.assertEqual(ret["status"], "fail")

		self.assert_last_request_headers("Authorization", "Bearer AUTH_TOKEN")

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

		self.assert_last_request_headers("Facility", "42")

		self.assertTrue(status)
		self.assertDictEqual(response, {k: True for k in inventory_map})

	def test_create_sales_invoice(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/invoice/createInvoiceBySaleOrderCode",
			status=200,
			json={"successful": True},
			match=[
				responses.json_params_matcher(
					{"saleOrderCode": "SO_CODE", "saleOrderItemCodes": ["1", "2", "3"]}
				)
			],
		)

		self.client.create_sales_invoice("SO_CODE", ["1", "2", "3"], "TEST")

		self.assert_last_request_headers("Facility", "TEST")

	def test_create_sales_invoice_with_shipping_package(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/shippingPackage/createInvoice",
			status=200,
			json={"successful": True},
			match=[responses.json_params_matcher({"shippingPackageCode": "SP_CODE"})],
		)

		self.client.create_invoice_by_shipping_code("SP_CODE", "TEST")

		self.assert_last_request_headers("Facility", "TEST")

	def test_create_invoice_and_label_with_shipping_package(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/shippingPackage/createInvoiceAndGenerateLabel",
			status=200,
			json={"successful": True},
			match=[
				responses.json_params_matcher(
					{"shippingPackageCode": "SP_CODE", "generateUniwareShippingLabel": True}
				)
			],
		)

		self.client.create_invoice_and_label_by_shipping_code("SP_CODE", "TEST")

	def test_create_invoice_and_assign_shipper(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/shippingPackage/createInvoiceAndAllocateShippingProvider",
			status=200,
			json={"successful": True},
			match=[responses.json_params_matcher({"shippingPackageCode": "SP_CODE"})],
		)

		self.client.create_invoice_and_assign_shipper("SP_CODE", "TEST")

		self.assert_last_request_headers("Facility", "TEST")

	def test_get_sales_invoice(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/invoice/details/get",
			status=200,
			json={"successful": True, "return": False},
			match=[responses.json_params_matcher({"shippingPackageCode": "PACKAGE_ID", "return": False})],
		)

		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/invoice/details/get",
			status=200,
			json={"successful": True, "return": True},
			match=[
				responses.json_params_matcher({"shippingPackageCode": "PACKAGE_ID_RETURN", "return": True})
			],
		)

		res = self.client.get_sales_invoice("PACKAGE_ID", "TEST")
		self.assertFalse(res["return"])

		res = self.client.get_sales_invoice("PACKAGE_ID_RETURN", "TEST", is_return=True)
		self.assertTrue(res["return"])

		self.assert_last_request_headers("Facility", "TEST")

	def test_get_inventory_snapshot(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/inventory/inventorySnapshot/get",
			status=200,
			json={"successful": True},
			match=[
				responses.json_params_matcher(
					{"itemTypeSKUs": ["BOOK", "KINDLE"], "updatedSinceInMinutes": 120}
				)
			],
		)

		self.client.get_inventory_snapshot(
			sku_codes=["BOOK", "KINDLE"], facility_code="TEST", updated_since=120
		)

		self.assert_last_request_headers("Facility", "TEST")

	def test_update_shipping_package(self):
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/shippingPackage/edit",
			status=200,
			json={"successful": True},
			match=[
				responses.json_params_matcher(
					{
						"shippingPackageCode": "SP_CODE",
						"shippingPackageTypeCode": "DEFAULT",
						"shippingBox": {"length": 100, "width": 200, "height": 300},
					}
				)
			],
		)

		self.client.update_shipping_package("SP_CODE", "TEST", "DEFAULT", length=100, width=200, height=300)
		self.assert_last_request_headers("Facility", "TEST")

	def test_get_invoice_label(self):
		self.responses.add(
			responses.GET,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/shipment/show?shippingPackageCodes=SP_CODE",
			status=200,
			body="pdf",
		)

		pdf = self.client.get_invoice_label("SP_CODE", "TEST")
		self.assertEqual(pdf, base64.b64encode(b"pdf"))

		self.assert_last_request_headers("Facility", "TEST")

	def test_bulk_import(self):
		from frappe.utils.file_manager import save_file

		from ecommerce_integrations.unicommerce.grn import create_auto_grn_import

		csv_file = b"a,b,c\n1,2,3"
		csv_filename = "test_file.csv"

		item = frappe.get_last_doc("Item")

		save_file(fname=csv_filename, content=csv_file, dt=item.doctype, dn=item.name)

		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/data/import/job/create",
			status=200,
			match=[
				query_param_matcher({"name": "Auto GRN Items", "importOption": "CREATE_NEW"}),
			],
			json={"successful": True},
		)

		resp = create_auto_grn_import(csv_filename, "TEST", client=self.client)

		self.assertEqual(resp.successful, True)
		self.assert_last_request_headers("Facility", "TEST")
