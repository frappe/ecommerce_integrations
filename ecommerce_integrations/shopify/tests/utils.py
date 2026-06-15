import sys
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
import shopify
from erpnext import get_default_cost_center
from pyactiveresource.activeresource import ActiveResource
from pyactiveresource.testing import http_fake

from ecommerce_integrations.shopify.constants import API_VERSION, SETTING_DOCTYPE
from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import ShopifySetting
from ecommerce_integrations.tests.utils import EcommerceTestSuite

# Following code is adapted from Shopify python api under MIT license with minor changes.

# Copyright (c) 2011 "JadedPixel inc."

# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:

# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


class ShopifyTestSuite(EcommerceTestSuite):
	@classmethod
	@patch.object(ShopifySetting, "_handle_webhooks", Mock())
	def setUpClass(cls):
		# Standard test records like _Test Company are bootstrapped
		# on import of EcommerceTestSuite
		super().setUpClass()

		setting = frappe.get_doc(SETTING_DOCTYPE)
		setting.update(
			{
				"enable_shopify": 1,
				"shopify_url": "frappetest.myshopify.com",
				"password": "supersecret",
				"shared_secret": "supersecret",
				"default_customer": "_Test Customer",
				"customer_group": "_Test Customer Group 1",
				"company": "_Test Company",
				"cost_center": get_default_cost_center("_Test Company"),
				"cash_bank_account": "_Test Bank - _TC",
				"price_list": "_Test Price List",
				"warehouse": "_Test Warehouse - _TC",
				"sales_order_series": "SAL-ORD-.YYYY.-",
				"sync_delivery_note": 1,
				"delivery_note_series": "MAT-DN-.YYYY.-",
				"sync_sales_invoice": 1,
				"sales_invoice_series": "SINV-.YY.-",
				"upload_erpnext_items": 1,
				"update_shopify_item_on_update": 1,
				"update_erpnext_stock_levels_to_shopify": 1,
				"doctype": "Shopify Setting",
				"shopify_warehouse_mapping": [
					{
						"shopify_location_id": "62279942297",
						"shopify_location_name": "WH 1",
						"erpnext_warehouse": "_Test Warehouse 1 - _TC",
					},
					{
						"shopify_location_id": "61724295321",
						"shopify_location_name": "WH 2",
						"erpnext_warehouse": "_Test Warehouse 2 - _TC",
					},
				],
			}
		).save(ignore_permissions=True)

		# persist the setting across per-test rollbacks done in tearDown
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		# Disable Shopify again so the committed setting does not leak into
		# other test suites, where item creation would trigger the sync hook.
		frappe.db.set_single_value(SETTING_DOCTYPE, "enable_shopify", 0)
		frappe.db.commit()  # nosemgrep

	def setUp(self):
		ActiveResource.site = None
		ActiveResource.headers = None

		shopify.ShopifyResource.clear_session()
		shopify.ShopifyResource.site = f"https://frappetest.myshopify.com/admin/api/{API_VERSION}"
		shopify.ShopifyResource.password = None
		shopify.ShopifyResource.user = None

		http_fake.initialize()
		self.http = http_fake.TestHandler
		self.http.set_response(Exception("Bad request"))
		self.http.site = "https://frappetest.myshopify.com"

	def load_fixture(self, name, extension="json"):
		return (Path(__file__).parent / "data" / f"{name}.{extension}").read_bytes()

	def fake(
		self,
		endpoint,
		body=None,
		method="GET",
		prefix=None,
		extension="json",
		url=None,
		headers=None,
		has_user_agent=True,
		code=200,
		response_headers=None,
	):
		body = body or self.load_fixture(endpoint)
		prefix = prefix or f"/admin/api/{API_VERSION}"
		extension = f".{extension}" if extension else ""

		if not url:
			url = f"https://frappetest.myshopify.com{prefix}/{endpoint}{extension}"

		request_headers = {}
		if has_user_agent:
			python_version = sys.version.split(" ", 1)[0]
			request_headers["User-agent"] = f"ShopifyPythonAPI/{shopify.VERSION} Python/{python_version}"

		request_headers.update(headers or {})

		self.http.respond_to(
			method,
			url,
			request_headers,
			body=body,
			code=code,
			response_headers=response_headers,
		)
