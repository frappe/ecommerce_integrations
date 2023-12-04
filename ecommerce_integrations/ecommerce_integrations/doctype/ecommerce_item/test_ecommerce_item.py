# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest

import frappe

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item


class TestEcommerceItem(unittest.TestCase):
	def tearDown(self):
		for d in frappe.get_list("Ecommerce Item"):
			frappe.get_doc("Ecommerce Item", d.name).delete()

	def test_duplicate(self):
		self._create_doc()
		self.assertRaises(frappe.DuplicateEntryError, self._create_doc)

	def test_duplicate_variants(self):
		self._create_variant_doc()
		self.assertRaises(frappe.DuplicateEntryError, self._create_variant_doc)

	def test_duplicate_sku(self):
		self._create_doc_with_sku()
		self.assertRaises(frappe.DuplicateEntryError, self._create_doc_with_sku)

	def test_is_synced(self):
		self._create_doc()
		self.assertTrue(ecommerce_item.is_synced("shopify", "T-SHIRT"))
		self.assertFalse(ecommerce_item.is_synced("shopify", "UNKNOWN ITEM"))

	def test_is_synced_variant(self):
		self._create_variant_doc()
		self.assertTrue(ecommerce_item.is_synced("shopify", "T-SHIRT", "T-SHIRT-RED"))
		self.assertFalse(ecommerce_item.is_synced("shopify", "T-SHIRT", "Unknown variant"))

	def test_is_synced_sku(self):
		self._create_doc_with_sku()
		self.assertTrue(ecommerce_item.is_synced("shopify", "T-SHIRT", sku="TEST_ITEM_1"))
		self.assertFalse(ecommerce_item.is_synced("shopify", "T-SHIRTX", sku="UNKNOWNSKU"))

	def test_get_erpnext_item(self):
		self._create_doc()
		a = ecommerce_item.get_erpnext_item("shopify", "T-SHIRT")
		b = frappe.get_doc("Item", "_Test Item")
		self.assertEqual(a.name, b.name)
		self.assertEqual(a.item_code, b.item_code)

		unknown = ecommerce_item.get_erpnext_item("shopify", "Unknown item")
		self.assertEqual(unknown, None)

	def test_get_erpnext_item_variant(self):
		self._create_variant_doc()
		a = ecommerce_item.get_erpnext_item("shopify", "T-SHIRT", "T-SHIRT-RED")
		b = frappe.get_doc("Item", "_Test Item 2")
		self.assertEqual(a.name, b.name)
		self.assertEqual(a.item_code, b.item_code)

	def test_get_erpnext_item_sku(self):
		self._create_doc_with_sku()
		a = ecommerce_item.get_erpnext_item("shopify", "T-SHIRT", sku="TEST_ITEM_1")
		b = frappe.get_doc("Item", "_Test Item")
		self.assertEqual(a.name, b.name)
		self.assertEqual(a.item_code, b.item_code)

	def _create_doc(self):
		"""basic test for creation of ecommerce item"""
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": "shopify",
				"integration_item_code": "T-SHIRT",
				"erpnext_item_code": "_Test Item",
			}
		).insert()

	def _create_variant_doc(self):
		"""basic test for creation of ecommerce item"""
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": "shopify",
				"integration_item_code": "T-SHIRT",
				"erpnext_item_code": "_Test Item 2",
				"has_variants": 0,
				"variant_id": "T-SHIRT-RED",
				"variant_of": "_Test Variant Item",
			}
		).insert()

	def _create_doc_with_sku(self):
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": "shopify",
				"integration_item_code": "T-SHIRT",
				"erpnext_item_code": "_Test Item",
				"sku": "TEST_ITEM_1",
			}
		).insert()
