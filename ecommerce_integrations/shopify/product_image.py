"""Copy Shopify product images into ERPNext Items as stored files."""

import os
from urllib.parse import urlparse

import requests
from shopify.collection import PaginatedIterator
from shopify.resources import Product

import frappe
from frappe import _

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.shopify.utils import create_shopify_log

DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_LOGGED_SKUS = 50
SYNC_ALL_METHOD = "ecommerce_integrations.shopify.product_image.sync_all_product_images"


@frappe.whitelist()
def enqueue_sync_all_product_images():
	frappe.only_for("System Manager")
	frappe.enqueue(SYNC_ALL_METHOD, queue="long", job_id=SYNC_ALL_METHOD, deduplicate=True)
	frappe.msgprint(
		_(
			"Image sync from Shopify started in the background. Check Ecommerce Integration Log for the result."
		)
	)


@temp_shopify_session
def sync_all_product_images():
	"""Download the image of every Shopify product into its linked or SKU-matched ERPNext Item."""
	summary = {"added": 0, "skipped": 0, "unmatched": []}

	for page in PaginatedIterator(Product.find(limit=250)):
		for product in page:
			for variant in product.variants:
				_sync_variant_image(product, variant, summary)

	unmatched = summary["unmatched"]
	create_shopify_log(
		status="Success",
		method=SYNC_ALL_METHOD,
		message=_("Images added: {0}, skipped: {1}, no matching ERPNext Item: {2} {3}").format(
			summary["added"], summary["skipped"], len(unmatched), ", ".join(unmatched[:MAX_LOGGED_SKUS])
		),
	)


def sync_item_image(item_code: str, image_src: str | None) -> bool:
	"""Store `image_src` as the Item's image. Returns True if a file was added.

	An Item that already has a stored file is left alone. A remote image URL
	(as set by the product import) is replaced with a stored file.
	"""
	if not image_src or _has_stored_image(item_code):
		return False

	file_url = _save_image_file(item_code, image_src)
	# set_value skips Item hooks, so the image is not uploaded back to Shopify.
	frappe.db.set_value("Item", item_code, "image", file_url)
	return True


def import_images_enabled() -> bool:
	return bool(frappe.db.get_single_value(SETTING_DOCTYPE, "import_product_images"))


def _sync_variant_image(product, variant, summary):
	item_code = _find_item_code(product, variant)
	if not item_code:
		summary["unmatched"].append(variant.sku or str(variant.id))
		return

	try:
		added = sync_item_image(item_code, _variant_image_src(product, variant))
	except Exception:
		frappe.log_error(title=_("Shopify image sync failed for Item {0}").format(item_code))
		added = False

	summary["added" if added else "skipped"] += 1


def _find_item_code(product, variant) -> str | None:
	linked = ecommerce_item.get_erpnext_item_code(
		MODULE_NAME, integration_item_code=str(product.id), variant_id=str(variant.id)
	) or ecommerce_item.get_erpnext_item_code(MODULE_NAME, integration_item_code=str(product.id))
	if linked:
		return linked

	sku = (variant.sku or "").strip()
	return sku if sku and frappe.db.exists("Item", sku) else None


def _variant_image_src(product, variant) -> str | None:
	# A variant's own image wins over the product's featured image.
	images = product.images or []
	for image in images:
		if variant.image_id and image.id == variant.image_id:
			return image.src
	return images[0].src if images else None


def _has_stored_image(item_code: str) -> bool:
	image = frappe.db.get_value("Item", item_code, "image")
	return bool(image) and not image.startswith(("http://", "https://"))


def _save_image_file(item_code: str, image_src: str) -> str:
	response = requests.get(image_src, timeout=DOWNLOAD_TIMEOUT_SECONDS)
	response.raise_for_status()

	extension = os.path.splitext(urlparse(image_src).path)[1] or ".jpg"
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.scrub(item_code)}{extension}",
			"attached_to_doctype": "Item",
			"attached_to_name": item_code,
			"attached_to_field": "image",
			"is_private": 0,
			"content": response.content,
		}
	)
	file.insert(ignore_permissions=True)
	return file.file_url
