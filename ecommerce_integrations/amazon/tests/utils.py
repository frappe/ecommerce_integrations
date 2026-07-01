import json
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

import frappe
import responses
from frappe.utils import getdate
from requests import request
from requests.exceptions import HTTPError

from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import AmazonRepository
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
from ecommerce_integrations.tests.utils import EcommerceTestSuite

COMPANY = "Amazon Test Company"
COMPANY_ABBR = "ATC"


@lru_cache
def get_fixtures():
	return json.loads((Path(__file__).parent / "fixtures" / "test_data.json").read_bytes())


def _make_record(doctype, filters, data):
	if not frappe.db.exists(doctype, filters):
		frappe.get_doc(data).insert(ignore_permissions=True)


def _setup_amazon_test_masters():
	_make_record(
		"Company",
		{"company_name": COMPANY},
		{
			"doctype": "Company",
			"company_name": COMPANY,
			"abbr": COMPANY_ABBR,
			"country": "India",
			"default_currency": "INR",
		},
	)
	_make_record(
		"Warehouse",
		{"warehouse_name": "Amazon Test Warehouse"},
		{
			"doctype": "Warehouse",
			"warehouse_name": "Amazon Test Warehouse",
			"company": COMPANY,
		},
	)
	_make_record(
		"Item Group",
		{"item_group_name": "Amazon Test Warehouse"},
		{
			"doctype": "Item Group",
			"item_group_name": "Amazon Test Warehouse",
		},
	)
	_setup_amazon_fiscal_years()


def _setup_amazon_fiscal_years():
	"""Ensure a Fiscal Year exists for every year used by the order fixtures.

	The mocked Amazon orders can fall outside erpnext's default test fiscal
	years, so create the fiscal year for each year present in the fixture
	dates (derived from the data, not hardcoded)."""
	orders = get_fixtures().get("get_orders_200", {}).get("json", {}).get("payload", {}).get("Orders", [])
	years = {
		getdate(order[field]).year
		for order in orders
		for field in ("PurchaseDate", "LatestShipDate")
		if order.get(field)
	}
	for year in years:
		if frappe.db.exists("Fiscal Year", str(year)):
			continue
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": str(year),
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			}
		).insert(ignore_if_duplicate=True)


class AmazonTestSuite(EcommerceTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_setup_amazon_test_masters()
		setup_custom_fields()

		# persist setup across per-test rollbacks done in tearDown
		frappe.db.commit()  # nosemgrep

	def load_fixture(self, name="test_data"):
		return (
			get_fixtures()
			if name == "test_data"
			else json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_bytes())
		)


class MockSPAPI(SPAPI):
	expected_response: ClassVar = {}

	@responses.activate
	def make_request(
		self,
		method: str = "GET",
		append_to_base_uri: str = "",
		params: dict | None = None,
		data: dict | None = None,
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


class MockFinances(Finances, MockSPAPI):
	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int | None = None, next_token: str | None = None
	) -> object:
		self.expected_response = get_fixtures().get("list_financial_events_by_order_id_200")
		return super().list_financial_events_by_order_id(order_id, max_results, next_token)


class MockOrders(Orders, MockSPAPI):
	def get_orders(
		self,
		created_after: str,
		created_before: str | None = None,
		last_updated_after: str | None = None,
		last_updated_before: str | None = None,
		order_statuses: list | None = None,
		marketplace_ids: list | None = None,
		fulfillment_channels: list | None = None,
		payment_methods: list | None = None,
		buyer_email: str | None = None,
		seller_order_id: str | None = None,
		max_results: int = 100,
		easyship_shipment_statuses: list | None = None,
		next_token: str | None = None,
		amazon_order_ids: list | None = None,
		actual_fulfillment_supply_source_id: str | None = None,
		is_ispu: bool = False,
		store_chain_store_id: str | None = None,
	) -> object:
		self.expected_response = get_fixtures().get("get_orders_200")
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

	def get_order_items(self, order_id: str, next_token: str | None = None) -> object:
		self.expected_response = get_fixtures().get("get_order_items_200")
		return super().get_order_items(order_id, next_token)


class MockCatalogItems(CatalogItems, MockSPAPI):
	def get_catalog_item(self, asin: str, marketplace_id: str | None = None) -> object:
		self.expected_response = get_fixtures().get("get_catalog_item_200")
		return super().get_catalog_item(asin, marketplace_id)


class MockAmazonSettings:
	def __init__(self) -> None:
		self.is_active = 1
		self.iam_arn = "********************"
		self.refresh_token = "********************"
		self.client_id = "********************"
		self.client_secret = "********************"
		self.aws_access_key = "********************"
		self.aws_secret_key = "********************"
		self.country = "US"
		self.company = COMPANY
		self.warehouse = f"Amazon Test Warehouse - {COMPANY_ABBR}"
		self.parent_item_group = "Amazon Test Warehouse"
		self.price_list = "Standard Selling"
		self.customer_group = "Individual"
		self.territory = "All Territories"
		self.customer_type = "Individual"
		self.market_place_account_group = f"Accounts Receivable - {COMPANY_ABBR}"
		self.after_date = "2000-07-23"
		self.taxes_charges = 1
		self.enable_sync = 1
		self.max_retry_limit = 3
		self.create_item_if_not_exists = 1
		self.amazon_fields_map = [
			frappe._dict({"amazon_field": "ASIN", "item_field": "item_code", "use_to_find_item_code": 1})
		]


class MockAmazonRepository(AmazonRepository):
	def __init__(self) -> None:
		self.amz_setting = MockAmazonSettings()
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
		# Mocked SP-API methods are deterministic, so no retry/backoff is needed here.
		return sp_api_method(**kwargs).get("payload")

	def get_finances_instance(self):
		return MockFinances(**self.instance_params)

	def get_orders_instance(self):
		return MockOrders(**self.instance_params)

	def get_catalog_items_instance(self):
		return MockCatalogItems(**self.instance_params)
