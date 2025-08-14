from time import process_time

import frappe
from frappe.exceptions import UniqueValidationError
from shopify.resources import Product

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME, ACCOUNT_DOCTYPE, SETTING_DOCTYPE
from ecommerce_integrations.shopify.product import ShopifyProduct

# constants
SYNC_JOB_NAME = "shopify.job.sync.all.products"
REALTIME_KEY = "shopify.key.sync.all.products"


@frappe.whitelist()
def get_shopify_products(from_=None, account=None):
	shopify_products = fetch_all_products(from_, account)
	return shopify_products


def fetch_all_products(from_=None, account=None):
	# format shopify collection for datatable

	collection = _fetch_products_from_shopify(from_, account=account)

	products = []
	for product in collection:
		d = product.to_dict()
		d["synced"] = ecommerce_item.is_synced(MODULE_NAME, str(product.id))
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
def _fetch_products_from_shopify(from_=None, limit=20, account=None):
	if from_:
		collection = Product.find(from_=from_)
	else:
		collection = Product.find(limit=limit)

	return collection


@frappe.whitelist()
def get_product_count(account=None):
	items = frappe.db.get_list("Item", {"variant_of": ["is", "not set"]})
	erpnext_count = len(items)

	sync_items = frappe.db.get_list("Ecommerce Item", {"variant_of": ["is", "not set"]})
	synced_count = len(sync_items)

	shopify_count = get_shopify_product_count(account=account)

	return {
		"shopifyCount": shopify_count,
		"syncedCount": synced_count,
		"erpnextCount": erpnext_count,
	}


@temp_shopify_session
def get_shopify_product_count(account=None):
	return Product.count()


@frappe.whitelist()
def sync_product(product, account=None):
	"""Sync a single product with account context."""
	shopify_product = ShopifyProduct(product, account=account)  # Pass account parameter
	shopify_product.sync_product()
	return True

@frappe.whitelist()
def resync_product(product, account=None):
	"""Resync a single product with account context."""
	return _resync_product(product, account=account)

@temp_shopify_session
def _resync_product(product, account=None):
	"""Internal resync with account context."""
	shopify_product = ShopifyProduct(product, account=account)  # Pass account parameter
	shopify_product.sync_product()
	return True

@frappe.whitelist()
def queue_sync_all_products(*args, **kwargs):
	account = kwargs.get('account')
	start_time = process_time()

	counts = get_product_count(account=account)
	publish("Syncing all products...")

	if counts["shopifyCount"] < counts["syncedCount"]:
		publish("⚠ Shopify has less products than ERPNext.")

	_sync = True
	collection = _fetch_products_from_shopify(limit=100, account=account)
	savepoint = "shopify_product_sync"
	while _sync:
		for product in collection:
			try:
				publish(f"Syncing product {product.id}", br=False)
				frappe.db.savepoint(savepoint)
				if ecommerce_item.is_synced(MODULE_NAME, str(product.id)):
					publish(f"Product {product.id} already synced. Skipping...")
					continue

				shopify_product = ShopifyProduct(product.id, account=account)
				shopify_product.sync_product(account=account)

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
			collection = _fetch_products_from_shopify(from_=collection.next_page_url, account=account)
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


@frappe.whitelist()
def get_shopify_accounts():
	"""Get all enabled Shopify accounts with legacy fallback support."""
	from ecommerce_integrations.shopify.utils import resolve_account_context
	
	try:
		# Get all enabled Shopify accounts
		accounts = frappe.get_all(
			ACCOUNT_DOCTYPE,
			filters={"enabled": 1},
			fields=["name", "account_title", "shop_domain", "company"]
		)
		
		# If no multi-tenant accounts found, check legacy setting
		if not accounts:
			try:
				legacy_setting = resolve_account_context()  # Gets legacy setting
				if legacy_setting.is_enabled():
					# Return legacy setting as a single account option
					return [{
						"name": "legacy",
						"shop_url": legacy_setting.shopify_url or "",
						"company": legacy_setting.company or "",
						"title": "Legacy Shopify Setting"
					}]
			except:
				# No legacy setting available
				pass
			
			return []
		
		# Format multi-tenant accounts for frontend display
		formatted_accounts = []
		for account in accounts:
			formatted_accounts.append({
				"name": account.name,
				"shop_url": f"https://{account.shop_domain}" if account.shop_domain else "",
				"company": account.company or "",
				"title": account.account_title or account.shop_domain
			})
		
		return formatted_accounts
		
	except Exception as e:
		frappe.log_error(
			message=f"Error fetching Shopify accounts: {str(e)}",
			title="Shopify Import Products Error"
		)
		return []
