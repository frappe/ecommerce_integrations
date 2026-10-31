import types
import unittest
from unittest.mock import MagicMock, patch

from shopify.collection import PaginatedCollection

from ecommerce_integrations.shopify import product
from ecommerce_integrations.shopify.page.shopify_import_products import shopify_import_products
from ecommerce_integrations.shopify.product import complete_variants, fetch_all_variants

NEXT_PAGE = "https://frappetest.myshopify.com/admin/api/2024-01/products/1/variants.json?page_info=p2"


class _Variant:
	def __init__(self, id):
		self.id = id

	def to_dict(self):
		return {"id": self.id}


def _page(ids, next_url=None):
	"""A real PaginatedCollection, as Variant.find returns it, with a Link header to the next page."""
	headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
	return PaginatedCollection(
		[_Variant(i) for i in ids], metadata={"resource_class": product.Variant, "headers": headers}
	)


class TestVariantsBeyondInlineLimit(unittest.TestCase):
	"""The REST product resource includes at most 100 variants inline."""

	def test_below_the_limit_the_inline_list_stays(self):
		shopify_product = {"id": 1, "variants": [{"id": i} for i in range(99)]}
		complete_variants(shopify_product, lambda: self.fail("must not fetch"))
		self.assertEqual(len(shopify_product["variants"]), 99)

	def test_at_the_limit_the_paginated_list_replaces_it(self):
		shopify_product = {"id": 1, "variants": [{"id": i} for i in range(100)]}
		complete_variants(shopify_product, lambda: [{"id": i} for i in range(108)])
		self.assertEqual([v["id"] for v in shopify_product["variants"]], list(range(108)))

	def test_a_product_without_variants_is_left_alone(self):
		shopify_product = {"id": 1}
		complete_variants(shopify_product, lambda: self.fail("must not fetch"))
		self.assertNotIn("variants", shopify_product)


class TestFetchAllVariants(unittest.TestCase):
	def test_follows_the_next_page_link_until_the_last_page(self):
		pages = {None: _page(range(250), NEXT_PAGE), NEXT_PAGE: _page(range(250, 260))}
		calls = []

		def find(**kwargs):
			calls.append(kwargs)
			return pages[kwargs.get("from_")]

		with patch.object(product.Variant, "find", side_effect=find):
			variants = fetch_all_variants(1)

		self.assertEqual([v["id"] for v in variants], list(range(260)))
		self.assertEqual(calls, [{"product_id": 1, "limit": 250}, {"from_": NEXT_PAGE}])


class TestResyncProduct(unittest.TestCase):
	"""A product imported with only its first 100 variants is repaired by a re-sync."""

	def test_resync_visits_variants_beyond_the_inline_list(self):
		inline = types.SimpleNamespace(to_dict=lambda: {"id": 1, "variants": [{"id": i} for i in range(100)]})
		visited = []

		class Recorder:
			def __init__(self, product_id, variant_id=None):
				visited.append(variant_id)

			def sync_product(self):
				pass

		with (
			patch.object(shopify_import_products.Product, "find", return_value=inline),
			patch.object(shopify_import_products, "fetch_all_variants", return_value=[{"id": i} for i in range(108)]),
			patch.object(shopify_import_products, "ShopifyProduct", Recorder),
			patch("frappe.db", types.SimpleNamespace(savepoint=MagicMock(), rollback=MagicMock())),
		):
			# Unwrapped: the session decorator needs a configured store.
			self.assertTrue(shopify_import_products._resync_product.__wrapped__(1))

		self.assertEqual(visited, list(range(108)))
