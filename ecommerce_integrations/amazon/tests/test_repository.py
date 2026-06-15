# Copyright (c) 2022, Frappe and Contributors
# See license.txt

from ecommerce_integrations.amazon.tests.utils import AmazonTestSuite, MockAmazonRepository


class TestAmazonRepository(AmazonTestSuite):
	def test_get_orders(self):
		amazon_repository = MockAmazonRepository()
		sales_orders = amazon_repository.get_orders("2000-07-23")
		self.assertEqual(len(sales_orders), 2)
