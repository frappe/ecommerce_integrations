import os
import sys
import unittest

import frappe
import shopify
from pyactiveresource.activeresource import ActiveResource
from pyactiveresource.testing import http_fake

from ecommerce_integrations.shopify.constants import API_VERSION, SETTING_DOCTYPE


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


class TestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		setting = frappe.get_doc(SETTING_DOCTYPE)

		setting.update({"shopify_url": "frappetest.myshopify.com",}).save(ignore_permissions=True)

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

	def load_fixture(self, name, format="json"):
		with open(os.path.dirname(__file__) + "/data/%s.%s" % (name, format), "rb") as f:
			return f.read()

	def fake(self, endpoint, **kwargs):
		body = kwargs.pop("body", None) or self.load_fixture(endpoint)
		format = kwargs.pop("format", "json")
		method = kwargs.pop("method", "GET")
		prefix = kwargs.pop("prefix", f"/admin/api/{API_VERSION}")

		if "extension" in kwargs and not kwargs["extension"]:
			extension = ""
		else:
			extension = ".%s" % (kwargs.pop("extension", "json"))

		url = "https://frappetest.myshopify.com%s/%s%s" % (prefix, endpoint, extension)
		try:
			url = kwargs["url"]
		except KeyError:
			pass

		headers = {}
		if kwargs.pop("has_user_agent", True):
			userAgent = "ShopifyPythonAPI/%s Python/%s" % (shopify.VERSION, sys.version.split(" ", 1)[0])
			headers["User-agent"] = userAgent

		try:
			headers.update(kwargs["headers"])
		except KeyError:
			pass

		code = kwargs.pop("code", 200)

		self.http.respond_to(
			method,
			url,
			headers,
			body=body,
			code=code,
			response_headers=kwargs.pop("response_headers", None),
		)
