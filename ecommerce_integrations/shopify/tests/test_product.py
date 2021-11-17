# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import frappe

from ecommerce_integrations.shopify.product import ShopifyProduct

from .utils import TestCase


class TestProduct(TestCase):
	def test_sync_single_product(self):
		self.fake("products/6732194021530", body=self.load_fixture("single_product"))

		product = ShopifyProduct(product_id="6732194021530", variant_id="39933951901850")

		product.sync_product()

		self.assertTrue(product.is_synced())

		item = product.get_erpnext_item()

		self.assertEqual(frappe.get_last_doc("Item").item_code, item.item_code)

		ecommerce_item_exists = frappe.db.exists("Ecommerce Item", {"erpnext_item_code": item.name})
		self.assertTrue(bool(ecommerce_item_exists))

	def test_sync_product_with_variants(self):
		self.fake("products/6704435495065", body=self.load_fixture("variant_product"))

		product = ShopifyProduct(product_id="6704435495065")

		product.sync_product()

		self.assertTrue(product.is_synced())

		item = product.get_erpnext_item()  # should return template item
		self.assertTrue(bool(item.has_variants))
		self.assertEqual(item.name, str(product.product_id))

		required_variants = [
			"39845261443225",
			"39845261475993",
			"39845261508761",
			"39845261541529",
			"39845261574297",
			"39845261607065",
			"39845261639833",
			"39845261672601",
			"39845261705369",
		]

		variants = frappe.db.get_list("Item", filters={"variant_of": item.name})
		ecom_variants = frappe.db.get_list(
			"Ecommerce Item", filters={"variant_of": item.name}, fields="erpnext_item_code"
		)

		created_variants = [v.name for v in variants]
		created_ecom_variants = [e.erpnext_item_code for e in ecom_variants]

		self.assertEqual(len(variants), 9)  # 3 * 3
		self.assertEqual(sorted(required_variants), sorted(created_variants))

		self.assertEqual(len(created_ecom_variants), 9)
		self.assertEqual(sorted(required_variants), sorted(created_ecom_variants))
