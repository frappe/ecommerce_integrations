# Copyright (c) 2021, Frappe and Contributors
# See license.txt

import unittest
import frappe

from ecommerce_integrations.shopify.product import ShopifyProduct
from .utils import load_json


class TestProduct(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		# clean up all existing Ecommerce Items and related item docs
		for d in frappe.get_list("Ecommerce Item"):
			try:
				doc = frappe.get_doc("Ecommerce Item", d.name)
				item_code = doc.erpnext_item_code
				doc.delete()
				frappe.get_doc("Item", item_code).delete()
			except frappe.exceptions.LinkExistsError:
				# TODO: better way to handle links
				continue

	def test_sync_single_product(self):
		product_dict = load_json("single_product.json")

		product = ShopifyProduct(product_dict["id"])
		product._make_item(product_dict)  # can't hit API in tests, hence using stored reponse

		self.assertTrue(product.is_synced())

		item = product.get_erpnext_item()

		ecommerce_item_exists = frappe.db.exists(
			"Ecommerce Item", {"erpnext_item_code": item.name}
		)
		self.assertTrue(ecommerce_item_exists)

	def test_sync_product_with_variants(self):
		product_dict = load_json("variant_product.json")

		product = ShopifyProduct(product_dict["id"])

		product._make_item(product_dict)

		self.assertTrue(product.is_synced())

		item = product.get_erpnext_item()  # should return template item
		self.assertTrue(bool(item.has_variants))
		self.assertEqual(item.name, str(product.product_id))


		required_variants = [str(v["id"]) for v in  product_dict["variants"]]

		variants = frappe.db.get_list("Item", filters={"variant_of": item.name})
		created_variants = [v.name for v in variants]

		self.assertEqual(len(variants), 9)  # 3 * 3
		self.assertEqual(sorted(required_variants), sorted(created_variants))
