import unittest

import frappe
from frappe import _

from ecommerce_integrations.patches.update_woocommerce_items import create_ecommerce_items
from ecommerce_integrations.woocommerce.constants import MODULE_NAME, PRODUCT_GROUP


class TestWooCommerceItemMigration(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

	def test_migrate_items(self):
		create_ecommerce_items()
		filters = {"item_group": _(PRODUCT_GROUP, frappe.get_single("System Settings").language or "en")}
		items = frappe.db.get_all("Item", filters=filters, fields=["*"])
		ecomm_items = frappe.db.get_all("Ecommerce Item", {"integration": MODULE_NAME})
		self.assertEqual(len(items), len(ecomm_items))
