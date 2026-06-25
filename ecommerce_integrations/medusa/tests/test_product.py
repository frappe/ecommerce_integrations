# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe

from ecommerce_integrations.medusa.product import MedusaProduct

from .utils import TestCase


class TestProduct(TestCase):
	def test_sync_single_product(self):
		product = MedusaProduct(
			"prod_01HSINGLE0000000000000001",
			variant_id="variant_01HSINGLE000000000000001",
			sku="MUG-TEST-001",
		)

		product.sync_product()

		self.assertTrue(product.is_synced())

		item = product.get_erpnext_item()
		self.assertFalse(bool(item.has_variants))
		self.assertEqual(item.item_code, "prod_01HSINGLE0000000000000001")

		ecommerce_item = frappe.db.get_value(
			"Ecommerce Item",
			{"integration": "medusa", "integration_item_code": "prod_01HSINGLE0000000000000001"},
			["erpnext_item_code", "sku"],
			as_dict=True,
		)
		self.assertTrue(bool(ecommerce_item))
		self.assertEqual(ecommerce_item.erpnext_item_code, item.name)
		self.assertEqual(ecommerce_item.sku, "MUG-TEST-001")

	def test_sync_product_with_variants(self):
		product = MedusaProduct("prod_01HVARIANT000000000000001")

		product.sync_product()

		self.assertTrue(product.is_synced())

		template = product.get_erpnext_item()  # should return the template item
		self.assertTrue(bool(template.has_variants))
		self.assertEqual(template.name, "prod_01HVARIANT000000000000001")

		variants = frappe.db.get_list("Item", filters={"variant_of": template.name})
		self.assertEqual(len(variants), 6)  # 3 sizes x 2 colours

		ecom_variants = frappe.db.get_list(
			"Ecommerce Item",
			filters={"variant_of": template.name, "integration": "medusa"},
			fields=["erpnext_item_code", "variant_id", "sku"],
		)
		self.assertEqual(len(ecom_variants), 6)

		# each Medusa variant id is linked to exactly one ERPNext variant item
		synced_variant_ids = {e.variant_id for e in ecom_variants}
		self.assertEqual(
			synced_variant_ids,
			{
				"variant_S_Red",
				"variant_S_Blue",
				"variant_M_Red",
				"variant_M_Blue",
				"variant_L_Red",
				"variant_L_Blue",
			},
		)

		synced_skus = {e.sku for e in ecom_variants}
		self.assertIn("TEE-M-RED", synced_skus)

	def test_single_product_sync_is_idempotent(self):
		def sync():
			p = MedusaProduct(
				"prod_01HSINGLE0000000000000001",
				variant_id="variant_01HSINGLE000000000000001",
				sku="MUG-TEST-001",
			)
			p.sync_product()
			return p

		sync()
		count_before = frappe.db.count(
			"Ecommerce Item",
			{"integration": "medusa", "integration_item_code": "prod_01HSINGLE0000000000000001"},
		)

		# second sync must not create a duplicate Ecommerce Item / Item
		sync()
		count_after = frappe.db.count(
			"Ecommerce Item",
			{"integration": "medusa", "integration_item_code": "prod_01HSINGLE0000000000000001"},
		)
		self.assertEqual(count_before, count_after)
