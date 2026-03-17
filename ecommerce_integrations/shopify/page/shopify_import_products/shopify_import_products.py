import datetime
import json
import os
import time
from time import process_time
from unittest import result

import frappe
import requests
from frappe import _
from frappe.exceptions import UniqueValidationError
from shopify import GraphQL

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import (
	ecommerce_item,
)
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME
from ecommerce_integrations.shopify.product import ShopifyProduct
from ecommerce_integrations.shopify.utils import create_shopify_log

# constants
SYNC_JOB_NAME = "shopify.job.sync.all.products"
REALTIME_KEY = "shopify.key.sync.all.products"
TEMP_DIR = frappe.get_site_path("private", "temp")
os.makedirs(TEMP_DIR, exist_ok=True)


@frappe.whitelist()
def get_shopify_products(cursor=None, direction="next"):
	shopify_products = fetch_all_products(cursor=cursor, direction=direction)
	return shopify_products


def fetch_all_products(cursor=None, direction="next"):
	"""Fetch paginated Shopify products."""

	response = _fetch_products_from_shopify(cursor=cursor, direction=direction)
	products_data = response.get("products", [])
	page_info = response.get("pageInfo", {})

	products = []

	for product in products_data:
		product["synced"] = is_synced(product["id"])
		products.append(product)

	return {
		"products": products,
		"nextCursor": page_info.get("endCursor"),
		"prevCursor": page_info.get("startCursor"),
		"pageInfo": {
			"hasNextPage": page_info.get("hasNextPage", False),
			"hasPreviousPage": page_info.get("hasPreviousPage", False),
		},
	}


@temp_shopify_session
def _fetch_products_from_shopify(cursor=None, direction="next", limit=20):
	"""
	Fetch products from Shopify with bidirectional pagination (forward/backward).

	Args:
	    cursor (str): Cursor for pagination.
	    direction (str): 'next' for forward, 'prev' for backward pagination.
	    limit (int): Number of products per page.

	Returns:
	    dict: {
	        "products": [...],
	        "pageInfo": {
	            "hasNextPage": bool,
	            "hasPreviousPage": bool,
	            "startCursor": str,
	            "endCursor": str
	        }
	    }
	"""

	if direction == "prev":
		query = """
        query ($last: Int!, $before: String) {
          products(last: $last, before: $before) {
            edges {
              cursor
              node {
                id
                title
                variants(first: 100) {
                  edges {
                    node {
                      id
                      title
                      sku
                    }
                  }
                }
              }
            }
            pageInfo {
              hasNextPage
              hasPreviousPage
              startCursor
              endCursor
            }
          }
        }
        """
		variables = {"last": limit, "before": cursor if cursor else None}
	else:
		query = """
        query ($first: Int!, $after: String) {
          products(first: $first, after: $after) {
            edges {
              cursor
              node {
                id
                title
                variants(first: 100) {
                  edges {
                    node {
                      id
                      title
                      sku
                    }
                  }
                }
              }
            }
            pageInfo {
              hasNextPage
              hasPreviousPage
              startCursor
              endCursor
            }
          }
        }
        """
		variables = {"first": limit, "after": cursor if cursor else None}

	response = GraphQL().execute(query, variables=variables)
	response_dict = json.loads(response)
	products_data = response_dict.get("data", {}).get("products", {})

	edges = products_data.get("edges", [])
	products = []

	for edge in edges:
		node = edge.get("node", {})

		product_id = node.get("id", "").split("/")[-1]

		variants = []
		for v in node.get("variants", {}).get("edges", []):
			variant_node = v.get("node", {})
			variant_id = variant_node.get("id", "").split("/")[-1]
			variants.append(
				{
					"id": variant_id,
					"title": variant_node.get("title"),
					"sku": variant_node.get("sku"),
				}
			)

		products.append({"id": product_id, "title": node.get("title"), "variants": variants})

	page_info = products_data.get("pageInfo", {})

	return {
		"products": products,
		"pageInfo": page_info,
	}


@frappe.whitelist()
def get_product_count():
	items = frappe.db.get_list("Item", {"variant_of": ["is", "not set"]})
	erpnext_count = len(items)

	sync_items = frappe.db.get_list("Ecommerce Item", {"variant_of": ["is", "not set"]})
	synced_count = len(sync_items)

	shopify_count = get_shopify_product_count()

	return {
		"shopifyCount": shopify_count,
		"syncedCount": synced_count,
		"erpnextCount": erpnext_count,
	}


@temp_shopify_session
def get_shopify_product_count():
	query = """
    {
      productsCount {
        count
      }
    }
    """
	response = GraphQL().execute(query)
	response_dict = json.loads(response)
	data = response_dict.get("data", {}).get("productsCount", {})
	count = data.get("count", 0)
	return count


@frappe.whitelist()
def sync_product(product):
	try:
		shopify_product = ShopifyProduct(product)
		shopify_product.sync_product()

		return True

	except Exception:
		frappe.db.rollback()

		return False


@frappe.whitelist()
def resync_product(product):
	return _resync_product(product)


@temp_shopify_session
def _resync_product(product):
	"""
	Resync a specific Shopify product using GraphQL.
	Automatically cleans gid:// IDs and fetches product + variants via GraphQL.
	"""

	savepoint = "shopify_resync_product"

	try:
		product_id = str(product).split("/")[-1]

		query = """
        query ($id: ID!) {
          product(id: $id) {
            id
            title
            variants(first: 100) {
              edges {
                node {
                  id
                  title
                }
              }
            }
          }
        }
        """

		variables = {"id": f"gid://shopify/Product/{product_id}"}

		response = GraphQL().execute(query, variables=variables)
		response_dict = json.loads(response)
		product_data = response_dict.get("data", {}).get("product")

		if not product_data:
			publish(f"❌ Product {product_id} not found in Shopify.", error=True)
			return False

		frappe.db.savepoint(savepoint)

		for variant_edge in product_data.get("variants", {}).get("edges", []):
			variant_node = variant_edge.get("node", {})
			variant_id = variant_node.get("id", "").split("/")[-1]

			shopify_product = ShopifyProduct(product_id, variant_id=variant_id)
			shopify_product.sync_product()

			publish(f"✅ Synced variant {variant_id} for product {product_id}", synced=True)

		return True

	except Exception as e:
		frappe.log_error(f"Shopify Resync Error: {e}", "Shopify Product Resync")
		frappe.db.rollback(save_point=savepoint)
		publish(f"❌ Error resyncing product {product}: {e}", error=True)
		return False


def is_synced(product):
	return ecommerce_item.is_synced(MODULE_NAME, integration_item_code=product)


@frappe.whitelist()
def import_all_products():
	"""Entry point: decide between realtime or bulk sync"""
	counts = get_product_count()
	total_shopify = counts.get("shopifyCount", 0)
	publish(f"Starting import of {total_shopify} products from Shopify...")

	if total_shopify > 2000:
		start_bulk_import()

	else:
		frappe.enqueue(
			queue_sync_all_products,
			queue="long",
			job_name=SYNC_JOB_NAME,
			key=REALTIME_KEY,
			timeout=360,
		)
		# Run directly (faster) for small stores


def start_bulk_import():
	"""Start a bulk import and monitor it"""
	publish("⚡ Starting Shopify bulk operation...")
	start_bulk_product_job()

	frappe.enqueue(
		monitor_bulk_job,
		queue="long",
		job_name=SYNC_JOB_NAME,
		key=REALTIME_KEY,
		is_async=False,
		timeout=8600000,
	)


@temp_shopify_session
def start_bulk_product_job():
	"""Start a Shopify bulk operation to fetch all products"""
	query = """
    mutation {
      bulkOperationRunQuery(
        query: \"""
        {
          products {
            edges {
              node {
                id
                title

              }
            }
          }
        }
        \"""
      ) {
        bulkOperation {
          id
          status
        }
        userErrors {
          field
          message
        }
      }
    }
    """
	response = json.loads(GraphQL().execute(query))
	if response.get("data", {}).get("bulkOperationRunQuery", {}).get("userErrors"):
		frappe.throw(
			_("Error while executing bulkOperation:", response["data"]["bulkOperationRunQuery"]["userErrors"])
		)
	return response


@temp_shopify_session
def check_bulk_status():
	"""Check current bulk operation status"""
	query = """
    {
      currentBulkOperation {
        id
        status
        errorCode
        completedAt
        objectCount
        fileSize
        url
      }
    }
    """
	response = json.loads(GraphQL().execute(query))
	return response.get("data", {}).get("currentBulkOperation")


def monitor_bulk_job(**kwargs):
	"""Monitor Shopify bulk job until completion"""
	publish("⏳ Waiting for Shopify bulk job to complete...")

	max_attempts = 120
	attempt = 0

	while attempt < max_attempts:
		info = check_bulk_status()
		if not info:
			publish("⚠️ No active bulk operation found.", error=True)
			create_shopify_log(
				status="Error",
				message="No active bulk operation found.",
				method="monitor_bulk_job",
			)
			return

		status = info.get("status")
		count = int(info.get("objectCount", 0))

		if status == "COMPLETED":
			publish(f"✅ Bulk job completed! Processing {count} products...")
			bulk_id = info.get("id")
			url = info.get("url")
			local_file = download_bulk_file(url, bulk_id)

			# Process the file in batches
			synced, failed = process_bulk_file_from_disk(local_file, bulk_id)
			publish(f"🎉 Bulk sync completed. Synced: {synced}, Failed: {failed}")
			create_shopify_log(
				status="Success",
				message="Bulk sync completed",
				method="monitor_bulk_job",
			)

			# Clean up local file
			if os.path.exists(local_file):
				os.remove(local_file)

			return

		elif status in ("FAILED", "CANCELED", "CANCELING"):
			publish(f"❌ Bulk job failed: {info.get('errorCode')}", error=True)
			create_shopify_log(
				status="Error",
				message=f"Bulk job failed: {info.get('errorCode')}",
				method="monitor_bulk_job",
			)
			return

		elif status == "RUNNING":
			publish(f"⏳ Processing... ({count} objects so far)", br=False)
			create_shopify_log(
				status="In Progress",
				message=f"Bulk job in progress... ({count} objects processed)",
				method="monitor_bulk_job",
			)

			# Adaptive polling — check faster for small jobs
			delay = 1 if count < 2000 else 5
			time.sleep(delay)
			attempt += 1

		else:
			time.sleep(2)
			attempt += 1

	publish("⏱️ Timeout: bulk job did not complete in expected time.", error=True)


BATCH_SIZE = 200  # Adjust based on your ERPNext worker limits


def download_bulk_file(url, bulk_id):
	"""
	Download Shopify bulk JSONL file to local temporary storage.

	"""

	safe_bulk_id = bulk_id.replace(":", "_").replace("/", "_")

	local_file = os.path.join(TEMP_DIR, f"shopify_bulk_{safe_bulk_id}.jsonl")

	create_shopify_log(
		status="In Progress",
		message=f"Downloading bulk file from url: {url}",
		method="download_bulk_file",
	)
	with requests.get(url, stream=True, timeout=300) as response:
		response.raise_for_status()
		with open(local_file, "wb") as f:
			for chunk in response.iter_content(chunk_size=1024 * 1024):
				if chunk:
					f.write(chunk)
	publish("✅ Bulk file downloaded successfully.")
	create_shopify_log(
		status="completed",
		message=f"Downloaded bulk file from url: {url}",
		method="download_bulk_file",
	)

	return local_file


def process_bulk_file_from_disk(file_path, bulk_id):
	"""
	Process Shopify bulk JSONL file in batches with checkpoints.
	"""
	# Ensure checkpoint doc exists
	if bulk_id and not frappe.db.exists("Shopify Bulk Sync Progress", {"bulk_id": bulk_id}):
		frappe.get_doc(
			{
				"doctype": "Shopify Bulk Sync Progress",
				"bulk_id": bulk_id,
				"last_synced_product_id": None,
			}
		).insert(ignore_permissions=True)

	last_synced_id = frappe.db.get_value(
		"Shopify Bulk Sync Progress", {"bulk_id": bulk_id}, "last_synced_product_id"
	)
	skip = bool(last_synced_id)
	batch = []
	synced_count = 0
	failed_count = 0
	seen_products = set()

	with open(file_path, encoding="utf-8") as f:
		for line in f:
			if not line.strip():
				continue
			product = json.loads(line)
			product_id = product["id"].split("/")[-1]

			# Skip already synced until checkpoint
			if skip:
				if product_id != last_synced_id:
					skip = False
				else:
					continue
				continue

			if product_id in seen_products or is_synced(product_id):
				continue
			seen_products.add(product_id)

			batch.append(product)

			if len(batch) >= BATCH_SIZE:
				b_synced, b_failed, last_id = process_batch(batch, bulk_id)
				synced_count += b_synced
				failed_count += b_failed
				batch = []

	# Process remaining products
	if batch:
		b_synced, b_failed, last_id = process_batch(batch, bulk_id)
		synced_count += b_synced
		failed_count += b_failed

	return synced_count, failed_count


def process_batch(batch, bulk_id=None):
	"""Sync a batch of products and update checkpoint."""
	synced_count = 0
	failed_count = 0
	last_synced_id = None

	for product in batch:
		try:
			product_id = product["id"].split("/")[-1]
			shopify_product = ShopifyProduct(product_id)
			shopify_product.sync_product()
			synced_count += 1
			last_synced_id = product_id

		except Exception as e:
			failed_count += 1
			frappe.log_error(
				message=f"Product {product_id} sync failed: {e}",
				title="Shopify Bulk Sync Error",
			)

	if all([bulk_id, last_synced_id]):
		frappe.db.set_value(
			"Shopify Bulk Sync Progress",
			{"bulk_id": bulk_id},
			"last_synced_product_id",
			last_synced_id,
		)
		# Commit DB and update checkpoint after each batch
		frappe.db.commit()

	return synced_count, failed_count, last_synced_id


def queue_sync_all_products(*args, **kwargs):
	start_time = process_time()

	counts = get_product_count()
	publish("Syncing all products...")

	if counts["shopifyCount"] < counts["syncedCount"]:
		publish("⚠ Shopify has fewer products than ERPNext.")

	savepoint = "shopify_product_sync"
	cursor = None
	has_next_page = True

	while has_next_page:
		collection = _fetch_products_from_shopify(cursor=cursor, direction="next", limit=100)
		products = collection.get("products", [])
		page_info = collection.get("pageInfo", {})

		for product in products:
			try:
				publish(f"Syncing product {product['id']}", br=False)
				frappe.db.savepoint(savepoint)

				if is_synced(product["id"]):
					publish(f"Product {product['id']} already synced. Skipping...")
					continue

				shopify_product = ShopifyProduct(product["id"])
				shopify_product.sync_product()

				publish(f"✅ Synced Product {product['id']}", synced=True)

			except UniqueValidationError as e:
				publish(f"❌ Error Syncing Product {product['id']} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

			except Exception as e:
				publish(f"❌ Error Syncing Product {product['id']} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

		# Commit after processing each Shopify page to persist progress
		# before fetching the next page
		frappe.db.commit()

		has_next_page = page_info.get("hasNextPage", False)
		cursor = page_info.get("endCursor") if has_next_page else None

	end_time = process_time()
	publish(f"🎉 Done in {end_time - start_time:.2f}s", done=True)
	create_shopify_log(
		status="Success",
		message=f"Completed syncing all products in {end_time - start_time:.2f}s",
		method="queue_sync_all_products",
	)

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
