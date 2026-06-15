from unittest.mock import patch

import frappe
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.utils import get_stock_balance

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.constants import MODULE_NAME
from ecommerce_integrations.unicommerce.inventory import update_inventory_on_unicommerce
from ecommerce_integrations.unicommerce.tests.test_client import UnicommerceClientTestSuite


class TestUnicommerceInventory(UnicommerceClientTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.items = ["_TestInventoryItemA", "_TestInventoryItemB", "_TestInventoryItemC"]

		with patch("ecommerce_integrations.shopify.product.upload_erpnext_item"):
			for item in cls.items:
				make_item(item)

		cls.ecom_items = [make_ecommerce_item(item) for item in cls.items]

		# persist setup across per-test rollbacks done in tearDown
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		for ecom_item in cls.ecom_items:
			if ecom_item:
				frappe.delete_doc("Ecommerce Item", ecom_item)

		# parent teardown commits, persisting the deletes above
		super().tearDownClass()

	def test_inventory_sync(self):
		"""requirement: When bin is changed the inventory sync should take place in next cycle"""

		# warehouses below are created along with the test company bootstrap
		make_stock_entry(item_code="_TestInventoryItemA", qty=10, to_warehouse="Stores - WP", rate=10)
		make_stock_entry(item_code="_TestInventoryItemB", qty=2, to_warehouse="Stores - WP", rate=10)
		make_stock_entry(
			item_code="_TestInventoryItemC", qty=42, to_warehouse="Work In Progress - WP", rate=10
		)

		stores_request = {
			"inventoryAdjustments": [
				make_inventory_adjustment(
					"_TestInventoryItemA", get_stock_balance("_TestInventoryItemA", "Stores - WP"), "A"
				),
				make_inventory_adjustment(
					"_TestInventoryItemB", get_stock_balance("_TestInventoryItemB", "Stores - WP"), "A"
				),
			]
		}
		wip_request = {
			"inventoryAdjustments": [
				make_inventory_adjustment(
					"_TestInventoryItemC",
					get_stock_balance("_TestInventoryItemC", "Work In Progress - WP"),
					"B",
				),
			]
		}

		self.fake("inventory/adjust/bulk", request_body=stores_request, json={"successful": True})
		self.fake("inventory/adjust/bulk", request_body=wip_request, json={"successful": True})

		# There's nothing to test after this.
		# responses library should match the correct response and fail if not done so.
		update_inventory_on_unicommerce(client=self.client, force=True)


def make_inventory_adjustment(sku, quantity, facility_code):
	return {
		"itemSKU": sku,
		"quantity": quantity,
		"shelfCode": "DEFAULT",
		"inventoryType": "GOOD_INVENTORY",
		"adjustmentType": "REPLACE",
		"facilityCode": facility_code,
	}


def make_ecommerce_item(item_code):
	if ecommerce_item.is_synced(MODULE_NAME, item_code):
		return None

	ecom_item = frappe.get_doc(
		{
			"doctype": "Ecommerce Item",
			"integration": MODULE_NAME,
			"erpnext_item_code": item_code,
			"integration_item_code": item_code,
		}
	).insert()

	return ecom_item.name
