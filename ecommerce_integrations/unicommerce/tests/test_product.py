import frappe
import responses

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.constants import MODULE_NAME
from ecommerce_integrations.unicommerce.product import (
	_build_unicommerce_item,
	_get_barcode_data,
	_get_item_group,
	_validate_create_brand,
	_validate_field,
	import_product_from_unicommerce,
)
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceProduct(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

	def test_import_missing_item_raises_error(self):
		"""requirement: when attempting to sync SKU that doesn't exist on Unicommerce system should throw error"""
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/catalog/itemType/get",
			status=200,
			json=self.load_fixture("missing_item"),
			match=[responses.json_params_matcher({"skuCode": "MISSING"})],
		)
		self.assertRaises(frappe.ValidationError, import_product_from_unicommerce, "MISSING", self.client)

		log = frappe.get_last_doc("Ecommerce Integration Log", filters={"integration": "unicommerce"})
		self.assertTrue("Failed to import" in log.message, "Logging for missing item not working")

	def test_import_item_from_unicommerce(self):
		"""requirement: When syncing correct item system creates item in erpnext and Ecommerce item for it"""
		code = "TITANIUM_WATCH"

		import_product_from_unicommerce(code, self.client)

		self.assertTrue(bool(frappe.db.exists("Item", code)))
		self.assertTrue(ecommerce_item.is_synced(MODULE_NAME, code))
		item = ecommerce_item.get_erpnext_item(MODULE_NAME, code)
		self.assertEqual(item.name, code)

		expected_item = {
			"item_code": "TITANIUM_WATCH",
			"item_group": "Products",
			"item_name": "TITANIUM WATCH",
			"description": "This is a watch.",
			"weight_per_unit": 1000,
			"weight_uom": "Gram",
			"brand": "TITANIUM",
			"shelf_life_in_days": 0,
			"disabled": 0,
			"image": "https://user-images.githubusercontent.com/9079960/131f-650c52c07a0e.gif",
		}
		for field, value in expected_item.items():
			self.assertEqual(item.get(field), value)

		ean_barcode = item.barcodes[0]
		upc_barcode = item.barcodes[1]
		self.assertEqual(ean_barcode.barcode, "73513537")
		self.assertEqual(ean_barcode.barcode_type, "EAN")
		self.assertEqual(upc_barcode.barcode, "065100004327")
		self.assertEqual(upc_barcode.barcode_type, "UPC-A")

	def test_validate_brand(self):
		brand_name = "_Test Brand"
		frappe.db.sql("delete from tabBrand where name = %s", brand_name)

		_validate_create_brand(brand_name)

		brand = frappe.get_doc("Brand", brand_name)
		self.assertEqual(brand_name, brand.name)

	def test_validate_field(self):
		self.assertTrue(_validate_field("item_group", "Products"))
		self.assertTrue(_validate_field("item_name", "whatever"))  # not a link field
		self.assertFalse(_validate_field("weight_uom", "whatever"))
		self.assertFalse(_validate_field("whatever", "whatever"))

	def test_get_barcode_data(self):
		item = {"upc": "065100004327", "ean": "73513537"}

		barcodes = _get_barcode_data(item)
		types = [bc["barcode_type"] for bc in barcodes]
		values = [bc["barcode"] for bc in barcodes]

		self.assertEqual(types, ["EAN", "UPC-A"])
		self.assertEqual(values, ["73513537", "065100004327"])

	def test_get_item_group(self):
		self.assertEqual(_get_item_group("TESTCAT"), "Test category")
		self.assertEqual(_get_item_group("Products"), "Products")
		self.assertEqual(_get_item_group("Whatever"), "All Item Groups")

	def test_build_unicommerce_item(self):
		"""Build unicommerce item from recently synced uni item and compare if dicts are same"""

		code = "TITANIUM_WATCH"
		import_product_from_unicommerce(code, self.client)

		uni_item = _build_unicommerce_item("TITANIUM_WATCH")
		actual_item = self.load_fixture("simple_item")["itemTypeDTO"]

		for k, v in uni_item.items():
			self.assertEqual(actual_item[k], v)
