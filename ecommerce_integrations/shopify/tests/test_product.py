# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import frappe
from erpnext.controllers.item_variant import create_variant
from shopify.resources import Product

from ecommerce_integrations.shopify.product import (
	ShopifyProduct,
	map_erpnext_variant_to_shopify_variant,
)

from .utils import ShopifyTestSuite


class TestProduct(ShopifyTestSuite):
	def test_sync_single_product(self):
		self.fake("products/6732194021530", body=self.load_fixture("single_product"))

		product = ShopifyProduct(product_id="6732194021530", variant_id="39933951901850")
		product.sync_product()

		self.assertTrue(product.is_synced())

		item = product.get_erpnext_item()
		self.assertEqual(frappe.get_last_doc("Item").item_code, item.item_code)
		self.assertTrue(frappe.db.exists("Ecommerce Item", {"erpnext_item_code": item.name}))

	def test_sync_product_with_variants(self):
		self.fake("products/6704435495065", body=self.load_fixture("variant_product"))

		product = ShopifyProduct(product_id="6704435495065")
		product.sync_product()

		self.assertTrue(product.is_synced())

		template_item = product.get_erpnext_item()
		self.assertTrue(bool(template_item.has_variants))
		self.assertEqual(template_item.name, str(product.product_id))

		required_variants = sorted(
			[
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
		)

		created_variants = frappe.get_all("Item", filters={"variant_of": template_item.name}, pluck="name")
		created_ecom_variants = frappe.get_all(
			"Ecommerce Item", filters={"variant_of": template_item.name}, pluck="erpnext_item_code"
		)

		self.assertEqual(required_variants, sorted(created_variants))
		self.assertEqual(required_variants, sorted(created_ecom_variants))

	def test_variant_id_mapping(self):
		template_item = make_item()

		self.fake("products/6704435495065", body=self.load_fixture("variant_product"))
		product = ShopifyProduct(product_id="6704435495065", has_variants=1)
		product.sync_product()

		self.assertTrue(product.is_synced())

		shopify_product = Product.find(product.product_id)

		expected_variant_ids = {
			("L", "Green"): "39845261705369",
			("L", "Red"): "39845261639833",
			("M", "Green"): "39845261607065",
			("M", "Red"): "39845261541529",
		}

		for (size, colour), shopify_variant_id in expected_variant_ids.items():
			erpnext_variant = create_variant(
				template_item.item_code, {"Test Sync Size": size, "Test Sync Colour": colour}
			)
			self.assertEqual(
				map_erpnext_variant_to_shopify_variant(
					shopify_product, erpnext_variant, {"option1": size, "option2": colour}
				),
				shopify_variant_id,
			)


def create_item_attributes():
	attributes = {
		"Test Sync Size": ["XSL", "S", "M", "L", "XL", "2XL"],
		"Test Sync Colour": ["Red", "Green", "Blue"],
	}

	for priority, (attribute, values) in enumerate(attributes.items(), start=1):
		if frappe.db.exists("Item Attribute", attribute):
			continue

		frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": attribute,
				"priority": priority,
				"item_attribute_values": [{"attribute_value": value, "abbr": value} for value in values],
			}
		).insert()


def make_item():
	create_item_attributes()

	return frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": frappe.generate_hash(length=16),
			"item_group": "Products",
			"has_variants": 1,
			"attributes": [
				{"attribute": "Test Sync Size"},
				{"attribute": "Test Sync Colour"},
			],
		}
	).insert()
