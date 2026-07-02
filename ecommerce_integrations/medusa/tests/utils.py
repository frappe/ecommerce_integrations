# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import json
import os
from unittest.mock import patch

import frappe
from erpnext import get_default_cost_center
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.medusa.constants import SETTING_DOCTYPE

# The Medusa stock-location id used across the order/return fixtures. The warehouse
# mapping below points this at "_Test Warehouse 1 - _TC" so fulfillment/return code
# can resolve it to an ERPNext warehouse.
TEST_LOCATION_ID = "sloc_01HWH1"


def load_fixture(name, format="json"):
	"""Load a JSON (or other) fixture from ``tests/data/``."""
	path = os.path.join(os.path.dirname(__file__), "data", f"{name}.{format}")
	with open(path) as f:
		if format == "json":
			return json.load(f)
		return f.read()


class FakeMedusaClient:
	"""Drop-in replacement for ``connection.MedusaClient`` that never hits the network.

	Each method returns a deep copy of a local JSON fixture (so a test that mutates the
	returned dict can't leak into another). Mirrors the named-endpoint surface of the
	real client. Inventory pushes are recorded so tests can assert on them.

	The class also keeps a class-level ``calls`` log of ``update_inventory_level`` so
	tests can assert the exact ``(inventory_item_id, location_id, stocked_quantity)``
	tuples that were pushed without reaching into the mock internals.
	"""

	# product_id / order_id / return_id -> fixture name
	PRODUCT_FIXTURES = {
		"prod_01HSINGLE0000000000000001": "single_product",
		"prod_01HVARIANT000000000000001": "variant_product",
	}
	ORDER_FIXTURES = {
		"order_01HORDER00000000000000001": "order",
	}
	RETURN_FIXTURES = {
		"ret_01HRETURN00000000000000001": "return",
		"ret_01HRETURNFULL0000000000001": "return_full",
	}

	def __init__(self, *args, **kwargs):
		# instance-level record of inventory pushes
		self.inventory_pushes = []

	# -- low level (only what the sync code calls) -------------------------------

	def get(self, path, params=None):
		if path == "/returns":
			# order.py backfill fetches an order's returns from here
			return {"returns": [load_fixture("return")], "count": 1}
		# inventory.py resolves a variant SKU -> inventory_item_id via this endpoint
		if path == "/inventory-items":
			sku = (params or {}).get("sku")
			return {
				"inventory_items": [
					{
						"id": f"iitem_{sku}",
						"sku": sku,
						"location_levels": [
							{
								"location_id": TEST_LOCATION_ID,
								"stocked_quantity": 0,
								"reserved_quantity": 0,
							}
						],
					}
				]
			}
		return {}

	def post(self, path, body=None):
		return {}

	# -- named endpoints ---------------------------------------------------------

	def get_order(self, order_id, fields=None):
		name = self.ORDER_FIXTURES.get(order_id)
		return load_fixture(name) if name else {}

	def list_orders(self, params=None):
		yield load_fixture("order")

	def get_product(self, product_id):
		name = self.PRODUCT_FIXTURES.get(product_id)
		return load_fixture(name) if name else {}

	def list_products(self, params=None):
		yield load_fixture("single_product")
		yield load_fixture("variant_product")

	def create_product(self, body):
		FakeMedusaClient.created_products.append(body)
		return {
			"id": "prod_created",
			"variants": [{"id": "variant_created", "sku": body.get("variants", [{}])[0].get("sku")}],
		}

	def get_or_create_product_type(self, value):
		# deterministic fake id so tests can assert the type_id mapping
		return f"ptyp_{value}" if value else None

	def update_product(self, product_id, body):
		return {"id": product_id}

	def get_customer(self, customer_id):
		return {"id": customer_id}

	def get_return(self, return_id):
		name = self.RETURN_FIXTURES.get(return_id)
		return load_fixture(name) if name else {}

	def list_stock_locations(self):
		yield {"id": TEST_LOCATION_ID, "name": "WH 1"}

	def update_inventory_level(self, inventory_item_id, location_id, stocked_quantity):
		"""Record the push instead of calling Medusa."""
		self.inventory_pushes.append((inventory_item_id, location_id, stocked_quantity))
		FakeMedusaClient.calls.append((inventory_item_id, location_id, stocked_quantity))
		return {}


# class-level push / create-product logs, reset per-test in TestCase.setUp
FakeMedusaClient.calls = []
FakeMedusaClient.created_products = []


class TestCase(IntegrationTestCase):
	"""Base test case for the Medusa connector.

	* Builds + enables a ``Medusa Setting`` fixture once for the class.
	* Patches every ``MedusaClient`` reference in the connector package to the
	  ``FakeMedusaClient`` so auth/HTTP never reaches the network. We also rely on
	  ``frappe.flags.in_test`` (the Setting skips its live credential check then).
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		frappe.flags.in_test = True

		# The shared create_ecommerce_item() stamps item_defaults with
		# erpnext.get_default_company(). On a fresh site before_tests creates
		# "Wind Power LLC" as the global default, which doesn't own the fixture's
		# "_TC" warehouses. Pin the default company (and clear any stray global
		# default warehouse) so created items validate against the fixture company.
		frappe.db.set_single_value("Global Defaults", "default_company", "_Test Company")
		frappe.defaults.set_global_default("company", "_Test Company")
		frappe.db.set_single_value("Stock Settings", "default_warehouse", None)
		# let Delivery Notes submit without seeding stock for every fixture item
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
		frappe.clear_cache()

		setting = frappe.get_doc(SETTING_DOCTYPE)
		setting.update(
			{
				"doctype": SETTING_DOCTYPE,
				"enable_medusa": 1,
				"medusa_url": "https://medusa-test.local",
				"admin_api_key": "sk_test_dummy_admin_key",
				"webhook_secret": "whsec_test_dummy_secret",
				"company": "_Test Company",
				"cost_center": get_default_cost_center("_Test Company"),
				"cash_bank_account": "_Test Bank - _TC",
				"default_customer": "_Test Customer",
				"customer_group": "_Test Customer Group 1",
				"warehouse": "_Test Warehouse - _TC",
				"sales_order_series": "SAL-ORD-.YYYY.-",
				"delivery_note_series": "MAT-DN-.YYYY.-",
				"sales_invoice_series": "SINV-.YY.-",
				"sync_delivery_note": 1,
				"sync_sales_invoice": 1,
				"sync_returns": 1,
				"add_shipping_as_item": 0,
				"consolidate_taxes": 0,
				"use_price_list": 0,
				"default_sales_tax_account": _ensure_account("Medusa Sales Tax", "Tax"),
				"default_shipping_charges_account": _ensure_account("Medusa Shipping", "Income Account"),
				"upload_erpnext_items": 1,
				"update_medusa_item_on_update": 1,
				"sync_new_item_as_active": 1,
				"upload_variants_as_items": 1,
				"update_erpnext_stock_levels_to_medusa": 1,
				"inventory_sync_frequency": "60",
				"medusa_warehouse_mapping": [
					{
						"medusa_location_id": TEST_LOCATION_ID,
						"medusa_location_name": "WH 1",
						"erpnext_warehouse": "_Test Warehouse 1 - _TC",
					}
				],
			}
		).save(ignore_permissions=True)

		cls.setting = frappe.get_doc(SETTING_DOCTYPE)

	def setUp(self):
		# reset the shared inventory-push / create-product logs before every test
		FakeMedusaClient.calls = []
		FakeMedusaClient.created_products = []

		# The connector logs via create_medusa_log(), which COMMITS mid-handler, so any
		# Sales Order / Invoice / Item / Customer it creates survives IntegrationTestCase's
		# per-test rollback. Purge those artifacts so every test starts from a clean slate.
		self._purge_medusa_state()

		# erpnext loads test-record companies (e.g. "Test Quality Company") that set a
		# global default company/warehouse ("Wind Power LLC" / "Stores - TQC") not owned
		# by our fixture company. create_ecommerce_item() stamps item_defaults from these
		# globals, so pin them per-test to the fixture company's warehouse.
		frappe.defaults.set_global_default("company", "_Test Company")
		frappe.defaults.set_global_default("default_warehouse", "_Test Warehouse - _TC")

		# patch MedusaClient everywhere the connector imports/uses it
		self._patches = []
		for target in (
			"ecommerce_integrations.medusa.connection.MedusaClient",
			"ecommerce_integrations.medusa.order.MedusaClient",
			"ecommerce_integrations.medusa.product.MedusaClient",
			"ecommerce_integrations.medusa.inventory.MedusaClient",
		):
			try:
				p = patch(target, FakeMedusaClient)
				p.start()
				self._patches.append(p)
			except (AttributeError, ModuleNotFoundError):
				# module may import the symbol lazily / not at all
				continue

	def tearDown(self):
		for p in self._patches:
			p.stop()

	def load_fixture(self, name, format="json"):
		return load_fixture(name, format)

	def _purge_medusa_state(self):
		"""Delete every record the connector may have committed (via create_medusa_log)
		in a prior test, so tests are isolated despite those mid-handler commits.

		Uses raw ``frappe.db.delete`` (bypassing the docstatus / link checks) since the
		connector submits the documents; leftover ledger rows are harmless for the tests.
		"""
		for dt in ("Sales Invoice", "Delivery Note", "Sales Order"):
			names = frappe.get_all(dt, filters={"medusa_order_id": ("is", "set")}, pluck="name")
			if not names:
				continue
			for child in (df.options for df in frappe.get_meta(dt).get_table_fields()):
				frappe.db.delete(child, {"parent": ("in", names)})
			frappe.db.delete(dt, {"name": ("in", names)})

		customers = frappe.get_all("Customer", filters={"medusa_customer_id": ("is", "set")}, pluck="name")
		if customers:
			frappe.db.delete("Dynamic Link", {"link_doctype": "Customer", "link_name": ("in", customers)})
			frappe.db.delete("Customer", {"name": ("in", customers)})

		# items created by the connector: linked via Ecommerce Item, plus any orphans
		# matching the fixtures' Medusa product/variant identifiers.
		item_codes = set(
			frappe.get_all("Ecommerce Item", filters={"integration": "medusa"}, pluck="erpnext_item_code")
		)
		for pattern in ("prod_01H%", "variant_%", "%-TEST-%", "TEE-%"):
			item_codes |= set(frappe.get_all("Item", filters={"item_code": ("like", pattern)}, pluck="name"))
		item_codes |= set(
			frappe.get_all("Item", filters={"item_name": ("like", "Medusa Test%")}, pluck="name")
		)
		item_codes = [c for c in item_codes if c]

		frappe.db.delete("Ecommerce Item", {"integration": "medusa"})
		if item_codes:
			frappe.db.delete("Bin", {"item_code": ("in", item_codes)})
			frappe.db.delete("Stock Ledger Entry", {"item_code": ("in", item_codes)})
			frappe.db.delete("Item", {"name": ("in", item_codes)})

		frappe.db.delete("Ecommerce Integration Log", {"integration": "medusa"})
		frappe.db.commit()


def _ensure_account(account_name, account_type):
	"""Create (or fetch) a leaf Account under the test company for tax/shipping defaults."""
	company = "_Test Company"
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"{account_name} - {abbr}"
	if frappe.db.exists("Account", name):
		return name

	parent = frappe.db.get_value(
		"Account",
		{
			"company": company,
			"is_group": 1,
			"root_type": "Income" if account_type == "Income Account" else "Liability",
		},
		"name",
	)
	if not parent:
		parent = frappe.db.get_value("Account", {"company": company, "is_group": 1}, "name")

	account = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": account_name,
			"company": company,
			"parent_account": parent,
			"account_type": account_type if account_type in ("Tax",) else "Income Account",
			"is_group": 0,
		}
	)
	account.flags.ignore_mandatory = True
	account.insert(ignore_permissions=True)
	return account.name
