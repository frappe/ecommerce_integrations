import json

import frappe

from ecommerce_integrations.unicommerce.cancellation_and_returns import (
	_delete_cancelled_items,
	_serialize_items,
)
from ecommerce_integrations.unicommerce.constants import ORDER_ITEM_CODE_FIELD
from ecommerce_integrations.unicommerce.tests.utils import UnicommerceTestSuite


class TestUnicommerceStatusUpdates(UnicommerceTestSuite):
	def test_serialization(self):
		so_item = frappe.new_doc("Sales Order Item")
		so_item._set_defaults()

		serialized_items = _serialize_items([so_item.as_dict()])

		self.assertIsInstance(serialized_items, str)
		self.assertEqual(len(json.loads(serialized_items)), 1)

	def test_delete_cancelled_items(self):
		cancelled_item = frappe.new_doc("Sales Order Item").update({ORDER_ITEM_CODE_FIELD: "cancelled"})
		active_item = frappe.new_doc("Sales Order Item").update({ORDER_ITEM_CODE_FIELD: "not cancelled"})

		items = _delete_cancelled_items([cancelled_item, active_item], cancelled_items=["cancelled"])

		self.assertEqual(len(items), 1)
		self.assertEqual(items[0].get(ORDER_ITEM_CODE_FIELD), "not cancelled")
