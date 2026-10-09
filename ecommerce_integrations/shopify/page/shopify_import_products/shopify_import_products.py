from time import process_time

import frappe
from frappe import _
from frappe.exceptions import UniqueValidationError
from shopify.resources import Product

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, MODULE_NAME
from ecommerce_integrations.shopify.product import ShopifyProduct
from ecommerce_integrations.shopify.utils import get_account_name, get_default_account

# constants
SYNC_JOB_NAME = "shopify.job.sync.all.products"
REALTIME_KEY = "shopify.key.sync.all.products"


def _get_account(shopify_account=None) -> str:
	"""Account the page works on: the one picked on the page, else the only enabled one."""
	if not shopify_account:
		return get_default_account().name

	if not frappe.db.get_value(ACCOUNT_DOCTYPE, shopify_account, "enable_shopify"):
		frappe.throw(_("Shopify Account {0} is not enabled.").format(frappe.bold(shopify_account)))
	return shopify_account


@frappe.whitelist()
def get_shopify_products(from_=None, shopify_account=None):
	shopify_products = fetch_all_products(from_, shopify_account=_get_account(shopify_account))
	return shopify_products


def fetch_all_products(from_=None, shopify_account=None):
	# format shopify collection for datatable

	collection = _fetch_products_from_shopify(from_, shopify_account=shopify_account)

	products = []
	for product in collection:
		d = product.to_dict()
		d["synced"] = is_synced(product.id, shopify_account)
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


@temp_shopify_session
def _fetch_products_from_shopify(from_=None, limit=20, shopify_account=None):
	if from_:
		collection = Product.find(from_=from_)
	else:
		collection = Product.find(limit=limit)

	return collection


@frappe.whitelist()
def get_product_count(shopify_account=None):
	shopify_account = _get_account(shopify_account)

	items = frappe.db.get_list("Item", {"variant_of": ["is", "not set"]})
	erpnext_count = len(items)

	sync_items = frappe.db.get_list(
		"Ecommerce Item",
		{"variant_of": ["is", "not set"], "integration": MODULE_NAME, "shopify_account": shopify_account},
	)
	synced_count = len(sync_items)

	shopify_count = get_shopify_product_count(shopify_account=shopify_account)

	return {
		"shopifyCount": shopify_count,
		"syncedCount": synced_count,
		"erpnextCount": erpnext_count,
	}


@temp_shopify_session
def get_shopify_product_count(shopify_account=None):
	return Product.count()


@frappe.whitelist()
def sync_product(product, shopify_account=None):
	try:
		shopify_product = ShopifyProduct(product, shopify_account=_get_account(shopify_account))
		shopify_product.sync_product()

		return True
	except Exception:
		frappe.db.rollback()
		return False


@frappe.whitelist()
def resync_product(product, shopify_account=None):
	return _resync_product(product, shopify_account=_get_account(shopify_account))


@temp_shopify_session
def _resync_product(product, shopify_account=None):
	savepoint = "shopify_resync_product"
	try:
		item = Product.find(product)

		frappe.db.savepoint(savepoint)
		for variant in item.variants:
			shopify_product = ShopifyProduct(product, variant_id=variant.id, shopify_account=shopify_account)
			shopify_product.sync_product()

		return True
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		return False


def is_synced(product, shopify_account=None):
	account = get_account_name(shopify_account)
	return ecommerce_item.is_synced(
		MODULE_NAME,
		integration_item_code=product,
		filters={"shopify_account": account} if account else None,
	)


@frappe.whitelist()
def import_all_products(shopify_account=None):
	frappe.enqueue(
		queue_sync_all_products,
		queue="long",
		job_name=SYNC_JOB_NAME,
		key=REALTIME_KEY,
		shopify_account=_get_account(shopify_account),
	)


def queue_sync_all_products(*args, shopify_account=None, **kwargs):
	start_time = process_time()
	shopify_account = _get_account(shopify_account)

	counts = get_product_count(shopify_account)
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
				if is_synced(product.id, shopify_account):
					publish(f"Product {product.id} already synced. Skipping...")
					continue

				shopify_product = ShopifyProduct(product.id, shopify_account=shopify_account)
				shopify_product.sync_product()

				publish(f"✅ Synced Product {product.id}", synced=True)

			except UniqueValidationError as e:
				publish(f"❌ Error Syncing Product {product.id} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

			except Exception as e:
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
	publish(f"🎉 Done in {end_time - start_time}s", done=True)
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
