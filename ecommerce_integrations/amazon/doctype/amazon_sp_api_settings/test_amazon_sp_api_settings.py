# Copyright (c) 2022, Frappe and Contributors
# See license.txt


import json
import os
import time
import unittest

import frappe
import responses
from frappe.exceptions import ValidationError
from requests import request
from requests.exceptions import HTTPError

from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
	AmazonRepository,
	validate_amazon_sp_api_credentials,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api import (
	SPAPI,
	CatalogItems,
	Finances,
	Orders,
	SPAPIError,
	Util,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings import (
	setup_custom_fields,
)

file_path = os.path.join(os.path.dirname(__file__), "test_data.json")
with open(file_path, "r") as json_file:
	try:
		DATA = json.load(json_file)
	except json.decoder.JSONDecodeError as e:
		frappe.throw(e)


class TestSPAPI(SPAPI):

	# Expected response after hitting the URL.
	expected_response = {}

	@responses.activate
	def make_request(
		self, method: str = "GET", append_to_base_uri: str = "", params: dict = None, data: dict = None,
	) -> object:
		if isinstance(params, dict):
			params = Util.remove_empty(params)
		if isinstance(data, dict):
			data = Util.remove_empty(data)

		if method == "GET":
			responses_method = responses.GET
		elif method == "POST":
			responses_method = responses.POST
		else:
			raise HTTPError("Method not supported!")

		url = self.endpoint + self.BASE_URI + append_to_base_uri

		responses.add(
			responses_method,
			url,
			status=self.expected_response.get("status", 200),
			json=self.expected_response.get("json", {}),
		)

		try:
			response = request(method=method, url=url, params=None, data=None)
			return response.json()
		except HTTPError as e:
			error = SPAPIError(str(e))
			error.response = e.response
			raise error


class TestFinances(Finances, TestSPAPI):
	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int = None, next_token: str = None
	) -> object:
		self.expected_response = DATA.get("list_financial_events_by_order_id_200")
		return super().list_financial_events_by_order_id(order_id, max_results, next_token)


class TestOrders(Orders, TestSPAPI):
	def get_orders(
		self,
		created_after: str,
		created_before: str = None,
		last_updated_after: str = None,
		last_updated_before: str = None,
		order_statuses: list = None,
		marketplace_ids: list = None,
		fulfillment_channels: list = None,
		payment_methods: list = None,
		buyer_email: str = None,
		seller_order_id: str = None,
		max_results: int = 100,
		easyship_shipment_statuses: list = None,
		next_token: str = None,
		amazon_order_ids: list = None,
		actual_fulfillment_supply_source_id: str = None,
		is_ispu: bool = False,
		store_chain_store_id: str = None,
	) -> object:
		self.expected_response = DATA.get("get_orders_200")
		return super().get_orders(
			created_after,
			created_before,
			last_updated_after,
			last_updated_before,
			order_statuses,
			marketplace_ids,
			fulfillment_channels,
			payment_methods,
			buyer_email,
			seller_order_id,
			max_results,
			easyship_shipment_statuses,
			next_token,
			amazon_order_ids,
			actual_fulfillment_supply_source_id,
			is_ispu,
			store_chain_store_id,
		)

	def get_order_items(self, order_id: str, next_token: str = None) -> object:
		self.expected_response = DATA.get("get_order_items_200")
		return super().get_order_items(order_id, next_token)


class TestCatalogItems(CatalogItems, TestSPAPI):
	def get_catalog_item(self, asin: str, marketplace_id: str = None,) -> object:
		self.expected_response = DATA.get("get_catalog_item_200")
		return super().get_catalog_item(asin, marketplace_id)


class TestAmazonSettings:
	def __init__(self) -> None:
		def get_company():
			company_name = frappe.db.get_value(
				"Company",
				{"company_name": "Amazon Test Company", "country": "India", "default_currency": "INR"},
				"company_name",
			)

			if not company_name:
				company = frappe.get_doc(
					{
						"doctype": "Company",
						"company_name": "Amazon Test Company",
						"abbr": "ATC",
						"country": "India",
						"default_currency": "INR",
					}
				)
				company.insert(ignore_permissions=True)
				company_name = company.company_name

			return company_name

		def get_warehouse():
			warehouse_name = frappe.db.get_value(
				"Warehouse", {"warehouse_name": "Amazon Test Warehouse",}, "warehouse_name"
			)

			if not warehouse_name:
				warehouse = frappe.get_doc(
					{
						"doctype": "Warehouse",
						"warehouse_name": "Amazon Test Warehouse",
						"company": "Amazon Test Company",
					}
				)
				warehouse.insert(ignore_permissions=True)
				warehouse_name = warehouse.warehouse_name

			return warehouse_name + " - ATC"

		def get_item_group():
			item_group_name = frappe.db.get_value(
				"Item Group", {"item_group_name": "Amazon Test Warehouse",}, "item_group_name"
			)

			if not item_group_name:
				item_group = frappe.get_doc(
					{"doctype": "Item Group", "item_group_name": "Amazon Test Warehouse",}
				)
				item_group.insert(ignore_permissions=True)
				item_group_name = item_group.item_group_name

			return item_group_name

		self.is_active = 1
		self.iam_arn = "********************"
		self.refresh_token = "********************"
		self.client_id = "********************"
		self.client_secret = "********************"
		self.aws_access_key = "********************"
		self.aws_secret_key = "********************"
		self.country = "US"
		self.company = get_company()
		self.warehouse = get_warehouse()
		self.parent_item_group = get_item_group()
		self.price_list = "Standard Selling"
		self.customer_group = "All Customer Groups"
		self.territory = "All Territories"
		self.customer_type = "Individual"
		self.market_place_account_group = "Accounts Receivable - ATC"
		self.after_date = "2000-07-23"
		self.taxes_charges = 1
		self.enable_sync = 1
		self.max_retry_limit = 3
		self.create_item_if_not_exists = 1
		self.amazon_fields_map = [
			frappe._dict({"amazon_field": "ASIN", "item_field": "item_code", "use_to_find_item_code": 1})
		]


class TestAmazonRepository(AmazonRepository):
	def __init__(self) -> None:
		self.amz_setting = TestAmazonSettings()
		self.instance_params = dict(
			iam_arn=self.amz_setting.iam_arn,
			client_id=self.amz_setting.client_id,
			client_secret=self.amz_setting.client_secret,
			refresh_token=self.amz_setting.refresh_token,
			aws_access_key=self.amz_setting.aws_access_key,
			aws_secret_key=self.amz_setting.aws_secret_key,
			country_code=self.amz_setting.country,
		)

	def call_sp_api_method(self, sp_api_method, **kwargs):
		max_retries = self.amz_setting.max_retry_limit

		for x in range(max_retries):
			try:
				result = sp_api_method(**kwargs)
				return result.get("payload")
			except Exception:
				time.sleep(3)
				continue

	def get_finances_instance(self):
		return TestFinances(**self.instance_params)

	def get_orders_instance(self):
		return TestOrders(**self.instance_params)

	def get_catalog_items_instance(self):
		return TestCatalogItems(**self.instance_params)


class TestAmazon(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		setup_custom_fields()

	def test_get_orders(self):
		amazon_repository = TestAmazonRepository()
		sales_orders = amazon_repository.get_orders("2000-07-23")
		self.assertEqual(len(sales_orders), 2)

	def test_validate_credentials(self):
		credentials = dict(
			iam_arn="********************",
			client_id="********************",
			client_secret="********************",
			refresh_token="********************",
			aws_access_key="********************",
			aws_secret_key="********************",
			country="US",
		)

		self.assertRaises(ValidationError, validate_amazon_sp_api_credentials, **credentials)
