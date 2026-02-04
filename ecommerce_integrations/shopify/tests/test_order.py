# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import json

from frappe.tests import IntegrationTestCase

from ecommerce_integrations.shopify.order import sync_sales_order


class TestOrder(IntegrationTestCase):
	def test_sync_with_variants(self):
		pass
