from time import process_time

import frappe
from frappe.exceptions import UniqueValidationError
from shopify.resources import Product

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME
from ecommerce_integrations.shopify.product import ShopifyProduct
from ecommerce_integrations.shopify.utils import get_user_company, get_user_shopify_account

# constants
SYNC_JOB_NAME = "shopify.job.sync.all.products"
REALTIME_KEY = "shopify.key.sync.all.products"


@frappe.whitelist()
def get_shopify_products(from_=None):
	shopify_products = fetch_all_products(from_)
	return shopify_products


def fetch_all_products(from_=None):
	# format shopify collection for datatable

	collection = _fetch_products_from_shopify(from_)

	products = []
	for product in collection:
		d = product.to_dict()
		d["synced"] = is_synced(product.id)
		products.append(d)

	next_url = None
	if collection.has_next_page():
		next_url = collection.next_page_url

	prev_url = None
	if collection.has_previous_page():
		prev_url = collection.previous_page_url

	return {
		"products": products,
		"nextUrl": next_url,
		"prevUrl": prev_url,
	}


@temp_shopify_session(shopify_account=None)
def _fetch_products_from_shopify(from_=None, limit=20):
	if from_:
		collection = Product.find(from_=from_)
	else:
		collection = Product.find(limit=limit)

	return collection


@frappe.whitelist()
def get_product_count(shopify_account=None):
	items = frappe.db.get_list("Item", {"variant_of": ["is", "not set"]})
	erpnext_count = len(items)

	sync_items = frappe.db.get_list("Ecommerce Item", {"variant_of": ["is", "not set"]})
	synced_count = len(sync_items)

	shopify_count = get_shopify_product_count(shopify_account=shopify_account)

	return {
		"shopifyCount": shopify_count,
		"syncedCount": synced_count,
		"erpnextCount": erpnext_count,
	}


@temp_shopify_session(shopify_account=None)
def get_shopify_product_count():
	return Product.count()


@frappe.whitelist()
def sync_product(product):
	try:
		shopify_product = ShopifyProduct(product, company=get_user_company(frappe.session.user))
		shopify_product.sync_product()

		return True
	except Exception:
		frappe.db.rollback()
		return False


@frappe.whitelist()
def resync_product(product):
	return _resync_product(product)


@temp_shopify_session(shopify_account=None)
def _resync_product(product):
	savepoint = "shopify_resync_product"
	try:
		item = Product.find(product)

		frappe.db.savepoint(savepoint)
		for variant in item.variants:
			shopify_product = ShopifyProduct(product, variant_id=variant.id, company=get_user_company(frappe.session.user))
			shopify_product.sync_product()

		return True
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		return False


def is_synced(product):
	return ecommerce_item.is_synced(MODULE_NAME, integration_item_code=product)


@frappe.whitelist()
def import_all_products():
	shopify_account = get_user_shopify_account()
	if not shopify_account:
		frappe.throw(
			"No Shopify account is linked to your user/company. Please configure Shopify Account and User Permissions before importing products."
		)

	frappe.enqueue(
		queue_sync_all_products,
		queue="long",
		job_name=SYNC_JOB_NAME,
		key=REALTIME_KEY,
		user=frappe.session.user,
		shopify_account=shopify_account.name,
	)


def queue_sync_all_products(*args, **kwargs):
	start_time = process_time()
	shopify_account = kwargs.get("shopify_account")
	synced_count = 0
	failed_count = 0
	failed_products = []

	counts = get_product_count(shopify_account=shopify_account)
	publish("Syncing all products...")

	if counts["shopifyCount"] < counts["syncedCount"]:
		publish("⚠ Shopify has less products than ERPNext.")

	_sync = True
	collection = _fetch_products_from_shopify(limit=100, shopify_account=shopify_account)
	savepoint = "shopify_product_sync"
	while _sync:
		for product in collection:
			try:
				publish(f"Syncing product {product.id}", br=False)
				frappe.db.savepoint(savepoint)
				if is_synced(product.id):
					publish(f"Product {product.id} already synced. Skipping...")
					continue

				shopify_product = ShopifyProduct(product.id, company=get_user_company(kwargs.get("user")))
				shopify_product.sync_product()
				synced_count += 1

				publish(f"✅ Synced Product {product.id}", synced=True)

			except UniqueValidationError as e:
				failed_count += 1
				failed_products.append(str(product.id))
				publish(f"❌ Error Syncing Product {product.id} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

			except Exception as e:
				failed_count += 1
				failed_products.append(str(product.id))
				publish(f"❌ Error Syncing Product {product.id} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

		if collection.has_next_page():
			frappe.db.commit()  # prevents too many write request error
			collection = _fetch_products_from_shopify(
				from_=collection.next_page_url, shopify_account=shopify_account
			)
		else:
			_sync = False

	end_time = process_time()
	if failed_count and not synced_count:
		publish(
			f"⚠ Sync finished with errors. Synced: {synced_count}, Failed: {failed_count}.",
			error=True,
			done=True,
		)
	elif failed_count:
		publish(
			f"⚠ Sync completed with partial errors. Synced: {synced_count}, Failed: {failed_count}.",
			error=True,
			done=True,
		)
	else:
		publish(f"🎉 Done in {end_time - start_time}s. Synced: {synced_count}", done=True)
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
