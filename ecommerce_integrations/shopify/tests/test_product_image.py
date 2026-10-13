# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.shopify.product import (
	ShopifyProduct,
	upload_erpnext_item,
	upload_imported_item,
)
from ecommerce_integrations.shopify.product_image import (
	_find_item_code,
	_sync_variant_image,
	sync_item_image,
)

from .utils import TestCase

# 1x1 transparent PNG
PNG_BYTES = bytes.fromhex(
	"89504e470d0a1a0a0000000d4948445200000001000000010806000000"
	"1f15c4890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)
IMAGE_SRC = "https://cdn.shopify.com/s/files/1/test/products/item.png?v=1"
# Own IDs so the existing product tests' links do not collide with this one.
PRODUCT_ID = "6732194099901"
VARIANT_ID = "39933951999901"
SKU = "_TEST-SHOPIFY-IMAGE-SKU"


def fake_download(*args, **kwargs):
	response = MagicMock()
	response.content = PNG_BYTES
	return response


class TestProductImage(TestCase):
	def setUp(self):
		super().setUp()
		self.item_codes = []

	def tearDown(self):
		frappe.db.set_single_value(SETTING_DOCTYPE, "import_product_images", 0)
		for code in self.item_codes:
			frappe.db.delete("Ecommerce Item", {"erpnext_item_code": code})
			frappe.db.delete("File", {"attached_to_doctype": "Item", "attached_to_name": code})
			frappe.delete_doc("Item", code, force=True)

	@patch("ecommerce_integrations.shopify.product_image.requests.get", side_effect=fake_download)
	def test_stores_image_file_on_item_without_image(self, mock_get):
		code = self.make_item("_Test Shopify Image Item 1")

		self.assertTrue(sync_item_image(code, IMAGE_SRC))

		image = frappe.db.get_value("Item", code, "image")
		self.assertTrue(image.startswith("/files/"))
		self.assertTrue(
			frappe.db.exists(
				"File", {"attached_to_doctype": "Item", "attached_to_name": code, "file_url": image}
			)
		)
		mock_get.assert_called_once()

	@patch("ecommerce_integrations.shopify.product_image.requests.get", side_effect=fake_download)
	def test_keeps_existing_stored_image(self, mock_get):
		code = self.make_item("_Test Shopify Image Item 2", image="/files/existing.png")

		self.assertFalse(sync_item_image(code, IMAGE_SRC))

		self.assertEqual(frappe.db.get_value("Item", code, "image"), "/files/existing.png")
		mock_get.assert_not_called()

	@patch("ecommerce_integrations.shopify.product_image.requests.get", side_effect=fake_download)
	def test_replaces_remote_image_url_with_stored_file(self, mock_get):
		code = self.make_item("_Test Shopify Image Item 3", image=IMAGE_SRC)

		self.assertTrue(sync_item_image(code, IMAGE_SRC))

		self.assertTrue(frappe.db.get_value("Item", code, "image").startswith("/files/"))

	def test_skips_when_shopify_has_no_image(self):
		code = self.make_item("_Test Shopify Image Item 4")

		self.assertFalse(sync_item_image(code, None))
		self.assertFalse(frappe.db.get_value("Item", code, "image"))

	@patch("ecommerce_integrations.shopify.product_image.requests.get", side_effect=fake_download)
	def test_import_links_existing_item_by_sku_and_stores_image(self, mock_get):
		code = self.make_item(SKU)
		frappe.db.set_single_value(SETTING_DOCTYPE, "import_product_images", 1)
		self.fake(f"products/{PRODUCT_ID}", body=self.product_fixture())

		ShopifyProduct(product_id=PRODUCT_ID, variant_id=VARIANT_ID).sync_product()

		linked = frappe.db.get_value(
			"Ecommerce Item",
			{"integration": MODULE_NAME, "integration_item_code": PRODUCT_ID},
			"erpnext_item_code",
		)
		self.assertEqual(linked, code)
		self.assertTrue(frappe.db.get_value("Item", code, "image").startswith("/files/"))

	def test_multi_variant_image_goes_to_sku_item_not_template(self):
		template = self.make_item("_Test Shopify Image Template")
		sku_item = self.make_item("_Test Shopify Image Variant SKU")
		self.link(template, product_id="9100", variant_id="")
		product = shopify_product("9100", [("9101", "_Test Shopify Image Variant SKU"), ("9102", "OTHER")])

		self.assertEqual(_find_item_code(product, product.variants[0]), sku_item)

	def test_multi_variant_without_match_is_not_guessed(self):
		template = self.make_item("_Test Shopify Image Template 2")
		self.link(template, product_id="9200", variant_id="")
		product = shopify_product("9200", [("9201", "NO-SUCH-SKU-1"), ("9202", "NO-SUCH-SKU-2")])

		self.assertIsNone(_find_item_code(product, product.variants[0]))

	def test_single_variant_falls_back_to_product_link(self):
		code = self.make_item("_Test Shopify Image Single")
		self.link(code, product_id="9300", variant_id="")
		product = shopify_product("9300", [("9301", "NO-SUCH-SKU-3")])

		self.assertEqual(_find_item_code(product, product.variants[0]), code)

	@patch("ecommerce_integrations.shopify.product_image.requests.get", side_effect=ConnectionError("down"))
	def test_failed_download_is_counted_as_failed(self, mock_get):
		code = self.make_item("_Test Shopify Image Failure")
		product = shopify_product("9400", [("9401", code)], image_src=IMAGE_SRC)
		summary = {"added": 0, "skipped": 0, "failed": [], "unmatched": []}

		_sync_variant_image(product, product.variants[0], summary)

		self.assertEqual(summary["failed"], [code])
		self.assertEqual(summary["skipped"], 0)

	def link(self, item_code, product_id, variant_id):
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": MODULE_NAME,
				"erpnext_item_code": item_code,
				"integration_item_code": product_id,
				"variant_id": variant_id,
				"sku": item_code,
			}
		).insert(ignore_permissions=True)

	def product_fixture(self):
		product = json.loads(self.load_fixture("single_product"))
		product["id"] = int(PRODUCT_ID)
		for variant in product["variants"]:
			variant.update({"id": int(VARIANT_ID), "product_id": int(PRODUCT_ID), "sku": SKU})
		for image in [*product.get("images", []), product["image"]]:
			image["product_id"] = int(PRODUCT_ID)
		return json.dumps(product).encode()

	def make_item(self, item_code, image=None):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_group": "_Test Item Group",
				"stock_uom": "Nos",
				"image": image,
			}
		)
		item.flags.from_integration = True  # keep the upload hook from calling Shopify
		item.insert(ignore_permissions=True)
		self.item_codes.append(item.name)
		return item.name


class TestDataImportUpload(TestCase):
	def setUp(self):
		super().setUp()
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test Shopify Data Import Item",
				"item_group": "_Test Item Group",
				"stock_uom": "Nos",
			}
		)
		item.flags.from_integration = True
		item.insert(ignore_permissions=True)
		# Fresh copy: the insert flag above would make the upload hook return early.
		self.item = frappe.get_doc("Item", item.name)
		frappe.flags.shopify_imported_items_queued = set()

	def tearDown(self):
		frappe.flags.in_import = False
		frappe.db.set_single_value(SETTING_DOCTYPE, "upload_items_created_by_data_import", 0)
		frappe.delete_doc("Item", self.item.name, force=True)

	@patch("ecommerce_integrations.shopify.product.frappe.enqueue")
	def test_imported_item_is_queued_once(self, mock_enqueue):
		frappe.db.set_single_value(SETTING_DOCTYPE, "upload_items_created_by_data_import", 1)
		frappe.flags.in_import = True

		# Data Import fires both after_insert and on_update for the same Item.
		upload_erpnext_item(self.item, "after_insert")
		upload_erpnext_item(self.item, "on_update")

		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args.kwargs["item_code"], self.item.name)

	@patch("ecommerce_integrations.shopify.product.frappe.enqueue")
	def test_imported_item_is_skipped_when_setting_is_off(self, mock_enqueue):
		frappe.flags.in_import = True

		upload_erpnext_item(self.item, "after_insert")

		mock_enqueue.assert_not_called()

	@patch("ecommerce_integrations.shopify.product.upload_erpnext_item")
	@patch("ecommerce_integrations.shopify.product.filelock")
	def test_variant_jobs_share_a_lock_per_template(self, mock_lock, mock_upload):
		with patch("ecommerce_integrations.shopify.product.frappe.get_doc") as mock_get_doc:
			mock_get_doc.return_value = SimpleNamespace(name="_Test Variant Red", variant_of="_Test Template")
			upload_imported_item(self.item.name)

		self.assertEqual(mock_lock.call_args.args[0], "shopify_upload_item__test_template")
		mock_upload.assert_called_once()

	@patch("ecommerce_integrations.shopify.product.upload_erpnext_item")
	def test_background_job_uploads_the_item(self, mock_upload):
		upload_imported_item(self.item.name)
		mock_upload.assert_called_once()
		self.assertEqual(mock_upload.call_args.args[0].name, self.item.name)

	@patch("ecommerce_integrations.shopify.product.upload_erpnext_item")
	def test_background_job_ignores_deleted_item(self, mock_upload):
		upload_imported_item("_Test Item That Does Not Exist")
		mock_upload.assert_not_called()


def shopify_product(product_id, variants, image_src=None):
	"""Minimal stand-in for a shopify.Product: id, variants (id, sku) and images."""
	images = [SimpleNamespace(id=1, src=image_src)] if image_src else []
	return SimpleNamespace(
		id=product_id,
		images=images,
		variants=[SimpleNamespace(id=vid, sku=sku, image_id=None) for vid, sku in variants],
	)
