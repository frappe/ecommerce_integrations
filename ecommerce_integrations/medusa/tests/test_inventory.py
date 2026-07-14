# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.utils import get_datetime

from ecommerce_integrations.medusa.inventory import update_inventory_on_medusa
from ecommerce_integrations.medusa.product import MedusaProduct

from .utils import TestCase

WAREHOUSE = "_Test Warehouse 1 - _TC"


class TestInventory(TestCase):
	def _sync_item(self):
		product = MedusaProduct(
			"prod_01HSINGLE0000000000000001",
			variant_id="variant_01HSINGLE000000000000001",
			sku="MUG-TEST-001",
		)
		product.sync_product()
		return product.get_erpnext_item()

	def _set_stock(self, item_code, qty):
		"""Force a Bin row whose ``modified`` is newer than the Ecommerce Item sync time."""
		bin_name = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": WAREHOUSE})
		if not bin_name:
			doc = frappe.get_doc(
				{
					"doctype": "Bin",
					"item_code": item_code,
					"warehouse": WAREHOUSE,
					"actual_qty": qty,
					"reserved_qty": 0,
				}
			)
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Bin", bin_name, {"actual_qty": qty, "reserved_qty": 0})
		frappe.db.commit()

	def _reset_sync_clock(self, ecom_item):
		# pretend the item was last synced at the epoch so the Bin counts as a delta
		frappe.db.set_value("Ecommerce Item", ecom_item, "inventory_synced_on", "1970-01-01 00:00:00")
		frappe.db.commit()

	def test_delta_push_and_sync_status_advances(self):
		item = self._sync_item()
		ecom_item = frappe.db.get_value(
			"Ecommerce Item", {"integration": "medusa", "erpnext_item_code": item.name}, "name"
		)

		self._set_stock(item.item_code, 17)
		self._reset_sync_clock(ecom_item)

		# force the scheduler throttle open
		frappe.db.set_value("Medusa Setting", "Medusa Setting", "last_inventory_sync", "1970-01-01 00:00:00")
		frappe.db.commit()

		from .utils import FakeMedusaClient

		FakeMedusaClient.calls = []
		update_inventory_on_medusa()

		# exactly one push, with the right (inventory_item_id, location_id, qty)
		self.assertTrue(FakeMedusaClient.calls)
		pushed = [c for c in FakeMedusaClient.calls if c[0] == "iitem_MUG-TEST-001"]
		self.assertEqual(len(pushed), 1)

		inventory_item_id, location_id, stocked_qty = pushed[0]
		self.assertEqual(inventory_item_id, "iitem_MUG-TEST-001")
		self.assertEqual(location_id, "sloc_01HWH1")
		self.assertEqual(stocked_qty, 17)  # actual_qty(17) - reserved_qty(0)

		# inventory_synced_on advanced past the epoch
		synced_on = frappe.db.get_value("Ecommerce Item", ecom_item, "inventory_synced_on")
		self.assertGreater(get_datetime(synced_on), get_datetime("1970-01-01 00:00:00"))

	def test_no_push_when_no_delta(self):
		item = self._sync_item()
		ecom_item = frappe.db.get_value(
			"Ecommerce Item", {"integration": "medusa", "erpnext_item_code": item.name}, "name"
		)

		self._set_stock(item.item_code, 5)

		# mark already-synced *now* so the Bin is not a delta
		frappe.db.set_value("Ecommerce Item", ecom_item, "inventory_synced_on", frappe.utils.now())
		frappe.db.set_value("Medusa Setting", "Medusa Setting", "last_inventory_sync", "1970-01-01 00:00:00")
		frappe.db.commit()

		from .utils import FakeMedusaClient

		FakeMedusaClient.calls = []
		update_inventory_on_medusa()

		pushed = [c for c in FakeMedusaClient.calls if c[0] == "iitem_MUG-TEST-001"]
		self.assertEqual(len(pushed), 0)
