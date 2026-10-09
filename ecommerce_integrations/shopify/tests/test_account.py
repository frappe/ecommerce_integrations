# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

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

	def test_default_account_is_the_only_enabled_one(self):
		make_account("one.myshopify.com", enabled=1)
		make_account("disabled.myshopify.com")

		self.assertEqual(get_default_account().name, "one.myshopify.com")

	def test_default_account_needs_an_enabled_account(self):
		make_account("disabled.myshopify.com")

		self.assertRaises(frappe.ValidationError, get_default_account)

	def test_only_one_account_can_be_enabled(self):
		make_account("one.myshopify.com", enabled=1)

		self.assertRaises(frappe.ValidationError, make_account, "two.myshopify.com", enabled=1)

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


class TestWebhookAccount(IntegrationTestCase):
	def setUp(self):
		for name in frappe.get_all(ACCOUNT_DOCTYPE, {"enable_shopify": 1}, pluck="name"):
			frappe.db.set_value(ACCOUNT_DOCTYPE, name, "enable_shopify", 0)

	def tearDown(self):
		frappe.db.rollback()

	def test_webhook_is_refused_while_its_store_is_ambiguous(self):
		from ecommerce_integrations.shopify.connection import _get_webhook_account

		make_account("one.myshopify.com", enabled=1)
		# a second enabled account cannot be saved, but data may still end up that way
		make_account("two.myshopify.com")
		frappe.db.set_value(ACCOUNT_DOCTYPE, "two.myshopify.com", "enable_shopify", 1)

		request = frappe._dict(data=b"{}")
		with patch("ecommerce_integrations.shopify.connection.create_shopify_log") as log:
			self.assertRaises(frappe.ValidationError, _get_webhook_account, request)
			log.assert_called_once()

	def test_webhook_belongs_to_the_only_enabled_account(self):
		from ecommerce_integrations.shopify.connection import _get_webhook_account

		make_account("one.myshopify.com", enabled=1)

		self.assertEqual(_get_webhook_account(frappe._dict(data=b"{}")).name, "one.myshopify.com")


class TestSettingMigration(IntegrationTestCase):
	"""The patch turns the configuration of the removed single into an account."""

	legacy = migration.LEGACY_SETTING_DOCTYPE

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
