import base64
import json

import frappe
import responses
from responses.matchers import json_params_matcher, query_param_matcher

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.tests.utils import UnicommerceTestSuite

BASE_URL = "https://demostaging.unicommerce.com"


def api_url(endpoint: str) -> str:
	return f"{BASE_URL}/services/rest/v1/{endpoint}"


class UnicommerceClientTestSuite(UnicommerceTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.client = UnicommerceAPIClient(BASE_URL, "AUTH_TOKEN")

	def setUp(self):
		self.responses = responses.RequestsMock()
		self.responses.start()

		self.fake(
			"catalog/itemType/get",
			request_body={"skuCode": "TITANIUM_WATCH"},
			json=self.load_fixture("simple_item"),
		)
		self.fake(
			"catalog/itemType/get",
			request_body={"skuCode": "MC-100"},
			json=self.load_fixture("product-MC-100"),
		)
		self.fake("oms/saleOrder/search", json=self.load_fixture("so_search_results"))

		def sales_order_mock(request):
			payload = json.loads(request.body)
			response_body = self.load_fixture(f"order-{payload['code']}")
			return (200, {}, json.dumps(response_body))

		self.responses.add_callback(
			responses.POST,
			api_url("oms/saleorder/get"),
			callback=sales_order_mock,
			content_type="application/json",
		)

		self.addCleanup(self.responses.stop)
		self.addCleanup(self.responses.reset)

	def fake(self, endpoint, method=responses.POST, request_body=None, **kwargs):
		"""Register a fake response for a Unicommerce API endpoint.

		`request_body` if provided is matched against the request's JSON payload."""
		if request_body is not None:
			kwargs.setdefault("match", [json_params_matcher(request_body)])

		self.responses.add(method, api_url(endpoint), **kwargs)

	def assert_last_request_headers(self, header, value):
		request_headers = self.responses.calls[0].request.headers
		self.assertEqual(request_headers[header], value)


class TestUnicommerceClient(UnicommerceClientTestSuite):
	def test_authorization_headers(self):
		"""requirement: client inserts bearer token in headers"""
		self.fake("catalog/itemType/get", request_body={"skuCode": "sku"}, json={"status": "fail"})

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
		self.fake(
			"catalog/itemType/get",
			request_body={"skuCode": "MISSING"},
			json=self.load_fixture("missing_item"),
		)

		item_data = self.client.get_unicommerce_item("MISSING")
		self.assertIsNone(item_data)

		log = frappe.get_last_doc("Ecommerce Integration Log", filters={"integration": "unicommerce"})
		self.assertIn("MISSING", log.response_data, "Logging for missing item not working")

	def test_get_sales_order(self):
		order_data = self.client.get_sales_order("SO5841")

		self.assertEqual(order_data["code"], "SO5841")
		self.assertEqual(order_data["displayOrderCode"], "SINV-00042")

	def test_create_update_item(self):
		item_dict = {"test_dict": True}
		self.fake(
			"catalog/itemType/createOrEdit",
			request_body={"itemType": item_dict},
			json={"successful": True},
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
		self.fake(
			"inventory/adjust/bulk",
			request_body=expected_body,
			json=self.load_fixture("bulk_inventory_response"),
		)

		inventory_map = {"A": 1, "B": 2}
		response, status = self.client.bulk_inventory_update("42", inventory_map)

		self.assert_last_request_headers("Facility", "42")

		self.assertTrue(status)
		self.assertDictEqual(response, {k: True for k in inventory_map})

	def test_create_sales_invoice(self):
		self.fake(
			"invoice/createInvoiceBySaleOrderCode",
			request_body={"saleOrderCode": "SO_CODE", "saleOrderItemCodes": ["1", "2", "3"]},
			json={"successful": True},
		)

		self.client.create_sales_invoice("SO_CODE", ["1", "2", "3"], "TEST")

		self.assert_last_request_headers("Facility", "TEST")

	def test_create_sales_invoice_with_shipping_package(self):
		self.fake(
			"oms/shippingPackage/createInvoice",
			request_body={"shippingPackageCode": "SP_CODE"},
			json={"successful": True},
		)

		self.client.create_invoice_by_shipping_code("SP_CODE", "TEST")

		self.assert_last_request_headers("Facility", "TEST")

	def test_create_invoice_and_label_with_shipping_package(self):
		self.fake(
			"oms/shippingPackage/createInvoiceAndGenerateLabel",
			request_body={"shippingPackageCode": "SP_CODE", "generateUniwareShippingLabel": True},
			json={"successful": True},
		)

		self.client.create_invoice_and_label_by_shipping_code("SP_CODE", "TEST")

	def test_create_invoice_and_assign_shipper(self):
		self.fake(
			"oms/shippingPackage/createInvoiceAndAllocateShippingProvider",
			request_body={"shippingPackageCode": "SP_CODE"},
			json={"successful": True},
		)

		self.client.create_invoice_and_assign_shipper("SP_CODE", "TEST")

		self.assert_last_request_headers("Facility", "TEST")

	def test_get_sales_invoice(self):
		self.fake(
			"invoice/details/get",
			request_body={"shippingPackageCode": "PACKAGE_ID", "return": False},
			json={"successful": True, "return": False},
		)
		self.fake(
			"invoice/details/get",
			request_body={"shippingPackageCode": "PACKAGE_ID_RETURN", "return": True},
			json={"successful": True, "return": True},
		)

		response = self.client.get_sales_invoice("PACKAGE_ID", "TEST")
		self.assertFalse(response["return"])

		response = self.client.get_sales_invoice("PACKAGE_ID_RETURN", "TEST", is_return=True)
		self.assertTrue(response["return"])

		self.assert_last_request_headers("Facility", "TEST")

	def test_get_inventory_snapshot(self):
		self.fake(
			"inventory/inventorySnapshot/get",
			request_body={"itemTypeSKUs": ["BOOK", "KINDLE"], "updatedSinceInMinutes": 120},
			json={"successful": True},
		)

		self.client.get_inventory_snapshot(
			sku_codes=["BOOK", "KINDLE"], facility_code="TEST", updated_since=120
		)

		self.assert_last_request_headers("Facility", "TEST")

	def test_update_shipping_package(self):
		self.fake(
			"oms/shippingPackage/edit",
			request_body={
				"shippingPackageCode": "SP_CODE",
				"shippingPackageTypeCode": "DEFAULT",
				"shippingBox": {"length": 100, "width": 200, "height": 300},
			},
			json={"successful": True},
		)

		self.client.update_shipping_package("SP_CODE", "TEST", "DEFAULT", length=100, width=200, height=300)
		self.assert_last_request_headers("Facility", "TEST")

	def test_get_invoice_label(self):
		self.fake(
			"oms/shipment/show?shippingPackageCodes=SP_CODE",
			method=responses.GET,
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

		self.fake(
			"data/import/job/create",
			match=[query_param_matcher({"name": "Auto GRN Items", "importOption": "CREATE_NEW"})],
			json={"successful": True},
		)

		response = create_auto_grn_import(csv_filename, "TEST", client=self.client)

		self.assertEqual(response.successful, True)
		self.assert_last_request_headers("Facility", "TEST")
