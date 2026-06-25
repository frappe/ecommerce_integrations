# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import copy

import frappe
from frappe.utils import flt

from ecommerce_integrations.medusa.constants import FULFILLMENT_ID_FIELD, ORDER_ID_FIELD
from ecommerce_integrations.medusa.fulfillment import prepare_delivery_note
from ecommerce_integrations.medusa.order import sync_sales_order

from .utils import TestCase

ORDER_ID = "order_01HORDER00000000000000001"


class TestFulfillment(TestCase):
	def setUp(self):
		super().setUp()
		self.order = self.load_fixture("order")
		sync_sales_order(self.order)

	def _delivery_notes(self):
		return frappe.get_all(
			"Delivery Note",
			filters={ORDER_ID_FIELD: ORDER_ID},
			fields=["name", FULFILLMENT_ID_FIELD],
		)

	def test_fulfillment_creates_delivery_note(self):
		# the order fixture fulfills both lines (mug x2, tee x1)
		prepare_delivery_note(self.order)

		notes = self._delivery_notes()
		self.assertEqual(len(notes), 1)
		dn = frappe.get_doc("Delivery Note", notes[0].name)
		self.assertEqual(dn.docstatus, 1)
		self.assertEqual(dn.get(FULFILLMENT_ID_FIELD), "ful_01HFULFILL000000000000001")
		self.assertEqual(len(dn.items), 2)
		self.assertEqual(sum(flt(i.qty) for i in dn.items), 3)

	def test_fulfillment_is_idempotent(self):
		prepare_delivery_note(self.order)
		prepare_delivery_note(self.order)
		self.assertEqual(len(self._delivery_notes()), 1)

	def test_partial_fulfillment_ships_only_matched_lines(self):
		order = copy.deepcopy(self.order)
		order["fulfillments"] = [
			{
				"id": "ful_PARTIAL00000000000000001",
				"location_id": "sloc_01HWH1",
				"shipped_at": "2024-06-25T12:00:00.000Z",
				"items": [{"line_item_id": "item_mug", "quantity": 2}],
			}
		]
		prepare_delivery_note(order)

		notes = self._delivery_notes()
		self.assertEqual(len(notes), 1)
		dn = frappe.get_doc("Delivery Note", notes[0].name)
		# only the mug line ships; the unfulfilled tee line is not shipped
		self.assertEqual(len(dn.items), 1)
		self.assertEqual(flt(dn.items[0].qty), 2)

	def test_unmatched_fulfillment_does_not_ship(self):
		# a fulfillment whose line id matches no order line must NOT create a Delivery
		# Note that ships arbitrary rows; the handler fails safe and creates nothing.
		order = copy.deepcopy(self.order)
		order["fulfillments"] = [
			{
				"id": "ful_BADLINE000000000000000001",
				"location_id": "sloc_01HWH1",
				"items": [{"line_item_id": "does_not_exist", "quantity": 1}],
			}
		]
		prepare_delivery_note(order)
		self.assertFalse(
			any(
				n.get(FULFILLMENT_ID_FIELD) == "ful_BADLINE000000000000000001" for n in self._delivery_notes()
			)
		)
