import frappe

from ecommerce_integrations.unicommerce.cancellation_and_returns import (
	_delete_cancelled_items,
	_serialize_items,
)
from ecommerce_integrations.unicommerce.constants import ORDER_ITEM_CODE_FIELD
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceStatusUpdates(TestCaseApiClient):
	def test_serialization(self):
		si_item = frappe.new_doc("Sales Order Item")
		si_item._set_defaults()
		_serialize_items([si_item.as_dict()])

	def test_delete_cancelled_items(self):
		item1 = frappe.new_doc("Sales Order Item").update({ORDER_ITEM_CODE_FIELD: "cancelled"})
		item2 = frappe.new_doc("Sales Order Item").update({ORDER_ITEM_CODE_FIELD: "not cancelled"})

		cancelled_items = ["cancelled"]

		items = _delete_cancelled_items([item1, item2], cancelled_items)
		self.assertEqual(len(items), 1)
		self.assertEqual("not cancelled", items[0].get(ORDER_ITEM_CODE_FIELD))
