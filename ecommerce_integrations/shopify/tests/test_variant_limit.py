import unittest

from ecommerce_integrations.shopify.product import complete_variants


class TestVariantsBeyondInlineLimit(unittest.TestCase):
	"""The REST product resource includes at most 100 variants inline."""

	def test_below_the_limit_the_inline_list_stays(self):
		product = {"id": 1, "variants": [{"id": i} for i in range(99)]}
		complete_variants(product, lambda: self.fail("must not fetch"))
		self.assertEqual(len(product["variants"]), 99)

	def test_at_the_limit_the_paginated_list_replaces_it(self):
		product = {"id": 1, "variants": [{"id": i} for i in range(100)]}
		complete_variants(product, lambda: [{"id": i} for i in range(108)])
		self.assertEqual([v["id"] for v in product["variants"]], list(range(108)))

	def test_a_product_without_variants_is_left_alone(self):
		product = {"id": 1}
		complete_variants(product, lambda: self.fail("must not fetch"))
		self.assertNotIn("variants", product)
