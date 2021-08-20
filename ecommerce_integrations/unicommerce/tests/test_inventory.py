from unittest.mock import patch

import frappe
import responses
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.utils import get_stock_balance

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.constants import MODULE_NAME
from ecommerce_integrations.unicommerce.inventory import update_inventory_on_unicommerce
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceProduct(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.items = ["_TestInventoryItemA", "_TestInventoryItemB", "_TestInventoryItemC"]

		with patch("ecommerce_integrations.shopify.product.upload_erpnext_item"):
			for item in cls.items:
				make_item(item)

		cls.ecom_items = [make_ecommerce_item(item) for item in cls.items]

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		for ecom_item in cls.ecom_items:
			frappe.delete_doc("Ecommerce Item", ecom_item)

	def test_inventory_sync(self):
		"""requirement: When bin is changed the inventory sync should take place in next cycle"""

		# create stock entries for warehouses (warehouses are part of before_test hook in erpnext)
		make_stock_entry(item_code="_TestInventoryItemA", qty=10, to_warehouse="Stores - WP", rate=10)
		make_stock_entry(item_code="_TestInventoryItemB", qty=2, to_warehouse="Stores - WP", rate=10)
		make_stock_entry(
			item_code="_TestInventoryItemC", qty=42, to_warehouse="Work In Progress - WP", rate=10
		)

		wh1_request = {
			"inventoryAdjustments": [
				{
					"itemSKU": "_TestInventoryItemA",
					"quantity": get_stock_balance("_TestInventoryItemA", "Stores - WP"),
					"shelfCode": "DEFAULT",
					"inventoryType": "GOOD_INVENTORY",
					"adjustmentType": "REPLACE",
					"facilityCode": "A",
				},
				{
					"itemSKU": "_TestInventoryItemB",
					"quantity": get_stock_balance("_TestInventoryItemB", "Stores - WP"),
					"shelfCode": "DEFAULT",
					"inventoryType": "GOOD_INVENTORY",
					"adjustmentType": "REPLACE",
					"facilityCode": "A",
				},
			]
		}
		wh2_request = {
			"inventoryAdjustments": [
				{
					"itemSKU": "_TestInventoryItemC",
					"quantity": get_stock_balance("_TestInventoryItemC", "Work In Progress - WP"),
					"shelfCode": "DEFAULT",
					"inventoryType": "GOOD_INVENTORY",
					"adjustmentType": "REPLACE",
					"facilityCode": "B",
				},
			]
		}
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/inventory/adjust/bulk",
			status=200,
			json={"successful": True},
			match=[responses.json_params_matcher(wh1_request)],
		)
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/inventory/adjust/bulk",
			status=200,
			json={"successful": True},
			match=[responses.json_params_matcher(wh2_request)],
		)

		# There's nothing to test after this.
		# responses library should match the correct response and fail if not done so.
		update_inventory_on_unicommerce(client=self.client, force=True)


def make_ecommerce_item(item_code):

	if ecommerce_item.is_synced(MODULE_NAME, item_code):
		return

	ecom_item = frappe.get_doc(
		doctype="Ecommerce Item",
		integration=MODULE_NAME,
		erpnext_item_code=item_code,
		integration_item_code=item_code,
	).insert()
	return ecom_item.name
