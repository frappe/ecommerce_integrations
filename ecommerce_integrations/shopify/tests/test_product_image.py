# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import json
from unittest.mock import MagicMock, patch

import frappe

from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.shopify.product import (
	ShopifyProduct,
	upload_erpnext_item,
	upload_imported_item,
)
from ecommerce_integrations.shopify.product_image import sync_item_image

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
	def test_background_job_uploads_the_item(self, mock_upload):
		upload_imported_item(self.item.name)
		mock_upload.assert_called_once()
		self.assertEqual(mock_upload.call_args.args[0].name, self.item.name)

	@patch("ecommerce_integrations.shopify.product.upload_erpnext_item")
	def test_background_job_ignores_deleted_item(self, mock_upload):
		upload_imported_item("_Test Item That Does Not Exist")
		mock_upload.assert_not_called()
