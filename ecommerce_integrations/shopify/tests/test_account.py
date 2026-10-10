# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import base64
import hashlib
import hmac
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import get_decrypted_password, set_encrypted_password

from ecommerce_integrations.patches import migrate_shopify_setting_to_account as migration
from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, MODULE_NAME
from ecommerce_integrations.shopify.order import get_tax_account_head
from ecommerce_integrations.shopify.utils import get_default_account, resolve_account

ACCOUNT_MODULE = "ecommerce_integrations.shopify.doctype.shopify_account.shopify_account"


def make_account(shopify_url, enabled=0, **fields):
	# no calls to Shopify, and no custom field DDL that would commit the test transaction
	with (
		patch(f"{ACCOUNT_MODULE}.ShopifyAccount._handle_webhooks"),
		patch(f"{ACCOUNT_MODULE}.setup_custom_fields"),
	):
		account = frappe.get_doc(
			{
				"doctype": ACCOUNT_DOCTYPE,
				"shopify_url": shopify_url,
				"enable_shopify": enabled,
				"password": "token",
				"shared_secret": "secret",
				"is_old_data_migrated": 1,
				**fields,
			}
		)
		account.flags.ignore_links = True
		account.flags.ignore_mandatory = True
		return account.insert(ignore_permissions=True)


class TestShopifyAccount(IntegrationTestCase):
	def setUp(self):
		# every test starts without enabled accounts, so it can set up exactly what it needs
		for name in frappe.get_all(ACCOUNT_DOCTYPE, {"enable_shopify": 1}, pluck="name"):
			frappe.db.set_value(ACCOUNT_DOCTYPE, name, "enable_shopify", 0)

	def tearDown(self):
		frappe.db.rollback()

	def test_account_is_named_after_the_shop_domain(self):
		account = make_account(" HTTPS://Named.myshopify.com/admin ")

		self.assertEqual(account.name, "named.myshopify.com")
		self.assertEqual(account.shopify_url, "named.myshopify.com")

	def test_shop_url_stays_the_account_name(self):
		# webhooks are routed by the domain the account is named after, so the URL is
		# read-only after creation, and a changed value never diverges from the name
		self.assertTrue(frappe.get_meta(ACCOUNT_DOCTYPE).get_field("shopify_url").set_only_once)

		account = make_account("fixed.myshopify.com")
		account.shopify_url = "other.myshopify.com"
		account.save()

		self.assertEqual(account.name, "fixed.myshopify.com")
		self.assertEqual(account.shopify_url, "fixed.myshopify.com")

	def test_import_progress_is_published_per_account(self):
		from ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products import (
			publish,
		)

		with patch("frappe.publish_realtime") as publish_realtime:
			publish("Syncing", shopify_account="one.myshopify.com")

		self.assertEqual(
			publish_realtime.call_args.args[0], "shopify.key.sync.all.products.one.myshopify.com"
		)

	def test_default_account_is_the_only_enabled_one(self):
		make_account("one.myshopify.com", enabled=1)
		make_account("disabled.myshopify.com")

		self.assertEqual(get_default_account().name, "one.myshopify.com")

	def test_default_account_needs_an_enabled_account(self):
		make_account("disabled.myshopify.com")

		self.assertRaises(frappe.ValidationError, get_default_account)

	def test_several_accounts_can_be_enabled(self):
		make_account("one.myshopify.com", enabled=1)
		make_account("two.myshopify.com", enabled=1)

		# without a named account, nothing can pick one of them
		self.assertRaises(frappe.ValidationError, get_default_account)

	def test_disabling_does_not_depend_on_shopify_accepting_the_credentials(self):
		account = make_account("one.myshopify.com", enabled=1)
		account.append("webhooks", {"webhook_id": "1", "method": "orders/create"})
		account.db_update_all()
		account.reload()

		account.enable_shopify = 0
		with patch(
			"ecommerce_integrations.shopify.connection.unregister_webhooks",
			side_effect=Exception("401 Unauthorized"),
		):
			account.save()

		self.assertEqual(account.enable_shopify, 0)
		self.assertFalse(account.webhooks)

	def test_resolve_account_prefers_the_account_of_the_log(self):
		make_account("one.myshopify.com", enabled=1)
		make_account("retired.myshopify.com")
		log = frappe.get_doc(
			{
				"doctype": "Ecommerce Integration Log",
				"integration": MODULE_NAME,
				"shopify_account": "retired.myshopify.com",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(resolve_account(request_id=log.name).name, "retired.myshopify.com")
		self.assertEqual(resolve_account("one.myshopify.com").name, "one.myshopify.com")
		self.assertEqual(resolve_account().name, "one.myshopify.com")

	def test_tax_account_is_looked_up_on_the_orders_account(self):
		one = make_account(
			"one.myshopify.com",
			taxes=[{"shopify_tax": "VAT", "tax_account": "VAT One"}],
			default_shipping_charges_account="Shipping One",
		)
		two = make_account("two.myshopify.com", taxes=[{"shopify_tax": "VAT", "tax_account": "VAT Two"}])

		self.assertEqual(get_tax_account_head({"title": "VAT"}, one), "VAT One")
		self.assertEqual(get_tax_account_head({"title": "VAT"}, two), "VAT Two")
		self.assertEqual(
			get_tax_account_head({"title": "Standard"}, one, charge_type="shipping"), "Shipping One"
		)
		self.assertRaises(
			frappe.ValidationError, get_tax_account_head, {"title": "Standard"}, two, charge_type="shipping"
		)

	def test_sku_is_unique_per_account(self):
		make_account("one.myshopify.com")
		make_account("two.myshopify.com")

		def ecommerce_item(account, product_id):
			return frappe.get_doc(
				{
					"doctype": "Ecommerce Item",
					"integration": MODULE_NAME,
					"shopify_account": account,
					"erpnext_item_code": "_Test Item",
					"integration_item_code": product_id,
					"sku": "SKU-SHARED",
				}
			)

		ecommerce_item("one.myshopify.com", "1001").insert(ignore_links=True)
		ecommerce_item("two.myshopify.com", "2001").insert(ignore_links=True)
		self.assertRaises(
			frappe.DuplicateEntryError, ecommerce_item("one.myshopify.com", "1002").insert, ignore_links=True
		)


class TestSharedProducts(IntegrationTestCase):
	"""A product another store already sells is linked to the existing ERPNext items."""

	def setUp(self):
		for name in frappe.get_all(ACCOUNT_DOCTYPE, {"enable_shopify": 1}, pluck="name"):
			frappe.db.set_value(ACCOUNT_DOCTYPE, name, "enable_shopify", 0)
		make_account("first.myshopify.com")
		make_account("second.myshopify.com", enabled=1)

		# template with two variants, sold by the first store
		for item_code, variant_of in (
			("SHARED-TPL", None),
			("SHARED-S", "SHARED-TPL"),
			("SHARED-M", "SHARED-TPL"),
		):
			frappe.get_doc(
				{
					"doctype": "Item",
					"name": item_code,
					"item_code": item_code,
					"item_name": item_code,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"has_variants": 0 if variant_of else 1,
					"variant_of": variant_of,
				}
			).db_insert()
		for item_code, variant_id in (("SHARED-S", "11"), ("SHARED-M", "12")):
			frappe.get_doc(
				{
					"doctype": "Ecommerce Item",
					"integration": MODULE_NAME,
					"shopify_account": "first.myshopify.com",
					"erpnext_item_code": item_code,
					"integration_item_code": "1",
					"variant_id": variant_id,
					"sku": item_code,
					"variant_of": "SHARED-TPL",
				}
			).insert()

	def tearDown(self):
		frappe.db.rollback()

	def product(self, *skus):
		return {
			"id": 2,
			"options": [{"name": "Size", "values": ["S", "M"]}],
			"variants": [{"id": 20 + i, "sku": sku} for i, sku in enumerate(skus)],
		}

	def links(self):
		return [
			(link.erpnext_item_code, link.variant_id, link.has_variants)
			for link in frappe.get_all(
				"Ecommerce Item",
				{"shopify_account": "second.myshopify.com", "integration_item_code": "2"},
				["erpnext_item_code", "variant_id", "has_variants"],
				order_by="erpnext_item_code",
			)
		]

	def test_variants_link_to_the_template_of_the_other_store(self):
		from ecommerce_integrations.shopify.product import ShopifyProduct

		product = ShopifyProduct(2, shopify_account="second.myshopify.com")
		product._link_existing_template(self.product("SHARED-S", "SHARED-M"))

		self.assertEqual(
			self.links(), [("SHARED-M", "21", 0), ("SHARED-S", "20", 0), ("SHARED-TPL", None, 1)]
		)

	def test_known_variants_are_linked_when_others_are_new(self):
		from ecommerce_integrations.shopify.product import ShopifyProduct

		product = ShopifyProduct(2, shopify_account="second.myshopify.com")
		product._link_existing_template(self.product("SHARED-S", "NEW-L"))

		# the new variant is left to the regular sync, which creates it under the linked template
		self.assertEqual(self.links(), [("SHARED-S", "20", 0), ("SHARED-TPL", None, 1)])

	def test_resync_adds_only_missing_links(self):
		from ecommerce_integrations.shopify.product import ShopifyProduct

		product = ShopifyProduct(2, shopify_account="second.myshopify.com")
		product._link_existing_template(self.product("SHARED-S"))
		product._link_existing_template(self.product("SHARED-S", "SHARED-M"))

		self.assertEqual(
			self.links(), [("SHARED-M", "21", 0), ("SHARED-S", "20", 0), ("SHARED-TPL", None, 1)]
		)

	def test_product_keeps_its_own_template(self):
		from ecommerce_integrations.shopify.product import ShopifyProduct

		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": MODULE_NAME,
				"shopify_account": "second.myshopify.com",
				"erpnext_item_code": "SHARED-S",
				"integration_item_code": "2",
				"has_variants": 1,
			}
		).insert()
		product = ShopifyProduct(2, shopify_account="second.myshopify.com")
		product._link_existing_template(self.product("SHARED-M"))

		self.assertEqual(self.links(), [("SHARED-S", None, 1)])

	def test_variants_of_different_templates_are_not_linked(self):
		from ecommerce_integrations.shopify.product import ShopifyProduct

		frappe.db.set_value("Item", "SHARED-M", "variant_of", "OTHER-TPL", update_modified=False)
		product = ShopifyProduct(2, shopify_account="second.myshopify.com")
		product._link_existing_template(self.product("SHARED-S", "SHARED-M"))

		self.assertEqual(self.links(), [])

	def test_importer_refuses_a_disabled_account(self):
		from ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products import (
			_get_account,
		)

		self.assertEqual(_get_account("second.myshopify.com"), "second.myshopify.com")
		self.assertRaises(frappe.ValidationError, _get_account, "first.myshopify.com")


class TestWebhookRouting(IntegrationTestCase):
	"""Incoming webhooks are attributed to the account of the shop that sent them."""

	def setUp(self):
		for name in frappe.get_all(ACCOUNT_DOCTYPE, {"enable_shopify": 1}, pluck="name"):
			frappe.db.set_value(ACCOUNT_DOCTYPE, name, "enable_shopify", 0)
		make_account("one.myshopify.com", enabled=1, shared_secret="secret-one")
		make_account("two.myshopify.com", enabled=1, shared_secret="secret-two")
		make_account("retired.myshopify.com", shared_secret="secret-retired")

	def tearDown(self):
		frappe.db.rollback()

	def request(self, shop_domain, secret=None, body=b'{"id": 1}'):
		headers = {"X-Shopify-Shop-Domain": shop_domain} if shop_domain else {}
		if secret:
			digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
			headers["X-Shopify-Hmac-Sha256"] = base64.b64encode(digest).decode()
		return frappe._dict(data=body, headers=headers)

	def test_webhook_is_attributed_to_the_sending_shop(self):
		from ecommerce_integrations.shopify.connection import _get_webhook_account

		self.assertEqual(_get_webhook_account(self.request("two.myshopify.com")).name, "two.myshopify.com")
		self.assertEqual(_get_webhook_account(self.request("One.MyShopify.com")).name, "one.myshopify.com")

	def test_webhook_from_an_unknown_or_disabled_shop_is_refused(self):
		from ecommerce_integrations.shopify.connection import _get_webhook_account

		for shop_domain in ("unknown.myshopify.com", "retired.myshopify.com", None):
			with patch("ecommerce_integrations.shopify.connection.create_shopify_log") as log:
				self.assertRaises(frappe.ValidationError, _get_webhook_account, self.request(shop_domain))
				log.assert_called_once()

	def test_signature_is_checked_with_the_secret_of_the_sending_shop(self):
		from ecommerce_integrations.shopify.connection import _validate_request

		account = frappe.get_doc(ACCOUNT_DOCTYPE, "two.myshopify.com")

		valid = self.request("two.myshopify.com", secret="secret-two")
		_validate_request(valid, valid.headers["X-Shopify-Hmac-Sha256"], account)

		signed_by_other_shop = self.request("two.myshopify.com", secret="secret-one")
		with patch("ecommerce_integrations.shopify.connection.create_shopify_log"):
			self.assertRaises(
				frappe.ValidationError,
				_validate_request,
				signed_by_other_shop,
				signed_by_other_shop.headers["X-Shopify-Hmac-Sha256"],
				account,
			)


class TestSettingMigration(IntegrationTestCase):
	"""The patch turns the configuration of the removed single into an account."""

	legacy = migration.LEGACY_SETTING_DOCTYPE

	def setUp(self):
		# reload_doc may run DDL, which commits the test transaction in MariaDB
		reload_doc = patch.object(migration.frappe, "reload_doc")
		reload_doc.start()
		self.addCleanup(reload_doc.stop)

	def tearDown(self):
		frappe.db.rollback()

	def seed_legacy_setting(self, shopify_url):
		values = {
			"enable_shopify": "1",
			"shopify_url": f"https://{shopify_url}",
			"shared_secret": "legacy-secret",
			"company": "_Test Company",
			"sales_order_series": "SO-LEGACY-",
			"is_old_data_migrated": "1",
		}
		for field, value in values.items():
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values (%s, %s, %s)",
				(self.legacy, field, value),
			)
		set_encrypted_password(self.legacy, self.legacy, "legacy-token", "password")

		for child_doctype, row in (
			("Shopify Tax Account", {"shopify_tax": "VAT", "tax_account": "VAT - _TC"}),
			("Shopify Webhooks", {"webhook_id": "42", "method": "orders/create"}),
		):
			frappe.get_doc(
				{
					"doctype": child_doctype,
					"parent": self.legacy,
					"parenttype": self.legacy,
					"parentfield": "taxes" if child_doctype == "Shopify Tax Account" else "webhooks",
					**row,
				}
			).db_insert()

		item = frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": MODULE_NAME,
				"erpnext_item_code": "_Test Item",
				"integration_item_code": "legacy-product",
			}
		).insert(ignore_links=True)
		return item.name

	def test_single_becomes_an_account(self):
		shopify_url = "migration-test.myshopify.com"
		ecommerce_item = self.seed_legacy_setting(shopify_url)

		migration.execute()

		account = frappe.get_doc(ACCOUNT_DOCTYPE, shopify_url)
		self.assertEqual(account.enable_shopify, 1)
		self.assertEqual(account.company, "_Test Company")
		self.assertEqual(account.sales_order_series, "SO-LEGACY-")
		self.assertEqual(account.shared_secret, "legacy-secret")
		self.assertEqual([t.tax_account for t in account.taxes], ["VAT - _TC"])
		self.assertEqual([w.webhook_id for w in account.webhooks], ["42"])
		self.assertEqual(get_decrypted_password(ACCOUNT_DOCTYPE, shopify_url, "password"), "legacy-token")
		self.assertEqual(
			frappe.db.get_value("Ecommerce Item", ecommerce_item, "shopify_account"), shopify_url
		)
		self.assertFalse(frappe.db.sql("select 1 from `tabSingles` where doctype = %s", self.legacy))

	def test_without_legacy_configuration_nothing_is_created(self):
		accounts = frappe.db.count(ACCOUNT_DOCTYPE)

		migration.execute()

		self.assertEqual(frappe.db.count(ACCOUNT_DOCTYPE), accounts)
