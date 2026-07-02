# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe

from ecommerce_integrations.medusa.constants import ITEM_SELLING_RATE_FIELD
from ecommerce_integrations.medusa.product import (
	DEFAULT_OPTION_TITLE,
	DEFAULT_OPTION_VALUE,
	MedusaProduct,
	_get_variant_price,
	_setting_currency,
	upload_erpnext_item,
)

from .utils import FakeMedusaClient, TestCase, load_fixture


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

	def test_variant_price_selection(self):
		# fixture's M/Red variant carries a region-scoped price (price_rules) first,
		# then a plain eur and a plain usd price
		variant = next(
			v for v in load_fixture("variant_product")["variants"] if v["id"] == "variant_M_Red"
		)

		# region-scoped price is never picked as the base price
		self.assertEqual(_get_variant_price(variant), 22.99)
		# a currency preference picks the rule-free price in that currency
		self.assertEqual(_get_variant_price(variant, currency="USD"), 24.99)
		self.assertEqual(_get_variant_price(variant, currency="eur"), 22.99)
		# unknown currency falls back to the first rule-free price
		self.assertEqual(_get_variant_price(variant, currency="gbp"), 22.99)

		self.assertIsNone(_get_variant_price({"prices": []}))

	def test_upload_erpnext_item_payload(self):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "MEDUSA-UPLOAD-TEST-001",
				"item_name": "Medusa Upload Test",
				"item_group": "_Test Item Group",
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"weight_per_unit": 250,
				"weight_uom": "Gram",
			}
		)
		item.flags.from_integration = True  # keep the Item doc-hook from uploading on insert
		item.insert(ignore_if_duplicate=True)
		item.flags.from_integration = False
		item.set(ITEM_SELLING_RATE_FIELD, 19.99)

		upload_erpnext_item(item)

		self.assertEqual(len(FakeMedusaClient.created_products), 1)
		body = FakeMedusaClient.created_products[0]

		# AdminCreateProduct: type_id (not a v1-style nested type), default option, no nulls
		self.assertEqual(body["title"], "Medusa Upload Test")
		self.assertEqual(body["status"], "published")
		self.assertEqual(body["weight"], 250)
		self.assertNotIn("type", body)
		self.assertEqual(body["type_id"], "ptyp__Test Item Group")
		self.assertEqual(
			body["options"], [{"title": DEFAULT_OPTION_TITLE, "values": [DEFAULT_OPTION_VALUE]}]
		)
		self.assertNotIn(None, body.values())

		# AdminCreateProductVariant: required title/prices, options object keyed by title
		variant = body["variants"][0]
		self.assertEqual(variant["title"], "Medusa Upload Test")
		self.assertEqual(variant["sku"], "MEDUSA-UPLOAD-TEST-001")
		self.assertTrue(variant["manage_inventory"])
		self.assertEqual(variant["weight"], 250)
		self.assertEqual(variant["options"], {DEFAULT_OPTION_TITLE: DEFAULT_OPTION_VALUE})

		expected_currency = (_setting_currency(self.setting) or "usd").lower()
		self.assertEqual(
			variant["prices"], [{"amount": 19.99, "currency_code": expected_currency}]
		)

		# the created product is linked back via Ecommerce Item
		self.assertTrue(
			frappe.db.exists(
				"Ecommerce Item",
				{"integration": "medusa", "integration_item_code": "prod_created"},
			)
		)
