# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

from time import process_time

import frappe
from frappe.exceptions import UniqueValidationError

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.medusa.connection import MedusaClient
from ecommerce_integrations.medusa.constants import MODULE_NAME
from ecommerce_integrations.medusa.product import MedusaProduct

# constants
SYNC_JOB_NAME = "medusa.job.sync.all.products"
REALTIME_KEY = "medusa.key.sync.all.products"

PAGE_SIZE = 20


@frappe.whitelist()
def get_products(offset=0):
	"""Return one page of Medusa products formatted for the desk DataTable."""
	frappe.only_for("System Manager")
	offset = int(offset or 0)
	client = MedusaClient()
	data = client.get("/products", params={"limit": PAGE_SIZE, "offset": offset})

	rows = data.get("products", [])
	count = data.get("count", 0)

	products = []
	for product in rows:
		products.append(
			{
				"id": product.get("id"),
				"title": product.get("title"),
				"skus": ", ".join([v.get("sku") for v in product.get("variants") or [] if v.get("sku")]),
				"status": product.get("status"),
				"synced": is_synced(product.get("id")),
			}
		)

	next_offset = offset + PAGE_SIZE
	prev_offset = offset - PAGE_SIZE
	return {
		"products": products,
		"nextOffset": next_offset if next_offset < count else None,
		"prevOffset": prev_offset if prev_offset >= 0 else None,
	}


@frappe.whitelist()
def get_product_count():
	frappe.only_for("System Manager")
	items = frappe.db.get_list("Item", {"variant_of": ["is", "not set"]})
	erpnext_count = len(items)

	sync_items = frappe.db.get_list("Ecommerce Item", {"variant_of": ["is", "not set"]})
	synced_count = len(sync_items)

	medusa_count = get_medusa_product_count()

	return {
		"medusaCount": medusa_count,
		"syncedCount": synced_count,
		"erpnextCount": erpnext_count,
	}


def get_medusa_product_count():
	# Medusa list responses include the total ``count``; a 1-row probe is cheapest.
	data = MedusaClient().get("/products", params={"limit": 1, "offset": 0})
	return data.get("count", 0)


def is_synced(product_id) -> bool:
	return ecommerce_item.is_synced(MODULE_NAME, integration_item_code=str(product_id))


def _is_fully_synced(product) -> bool:
	"""True only when the template and every current variant are already linked, so a
	product that gained a variant since the last import is re-synced rather than skipped."""
	if not ecommerce_item.is_synced(MODULE_NAME, integration_item_code=str(product.get("id"))):
		return False
	for variant in product.get("variants") or []:
		if not ecommerce_item.is_synced(
			MODULE_NAME,
			integration_item_code=str(product.get("id")),
			variant_id=str(variant.get("id")),
			sku=variant.get("sku"),
		):
			return False
	return True


@frappe.whitelist()
def import_product(product_id):
	"""Sync a single Medusa product to ERPNext synchronously (table button)."""
	frappe.only_for("System Manager")
	try:
		MedusaProduct(product_id).sync_product()
		return True
	except Exception:
		frappe.db.rollback()
		return False


@frappe.whitelist()
def import_all_products():
	frappe.only_for("System Manager")
	frappe.enqueue(
		queue_sync_all_products,
		queue="long",
		job_name=SYNC_JOB_NAME,
		key=REALTIME_KEY,
	)


def queue_sync_all_products(*args, **kwargs):
	start_time = process_time()

	counts = get_product_count()
	publish("Syncing all products...")

	if counts["medusaCount"] < counts["syncedCount"]:
		publish("⚠ Medusa has fewer products than ERPNext.")

	savepoint = "medusa_product_sync"
	for product in MedusaClient().list_products():
		product_id = product.get("id")
		try:
			publish(f"Syncing product {product_id}", br=False)
			frappe.db.savepoint(savepoint)

			if _is_fully_synced(product):
				publish(f"Product {product_id} already synced. Skipping...")
				continue

			MedusaProduct(product).sync_product()
			publish(f"✅ Synced Product {product_id}", synced=True)

		except UniqueValidationError as e:
			publish(f"❌ Error Syncing Product {product_id} : {e!s}", error=True)
			frappe.db.rollback(save_point=savepoint)
			continue
		except Exception as e:
			publish(f"❌ Error Syncing Product {product_id} : {e!s}", error=True)
			frappe.db.rollback(save_point=savepoint)
			continue
		finally:
			frappe.db.commit()  # prevents too many write request errors

	end_time = process_time()
	publish(f"\U0001f389 Done in {end_time - start_time}s", done=True)
	return True


def publish(message, synced=False, error=False, done=False, br=True):
	frappe.publish_realtime(
		REALTIME_KEY,
		{
			"synced": synced,
			"error": error,
			"message": message + ("<br /><br />" if br else ""),
			"done": done,
		},
	)
