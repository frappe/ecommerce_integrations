# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.utils import flt

from ecommerce_integrations.medusa.constants import ORDER_ID_FIELD, ORDER_NUMBER_FIELD
from ecommerce_integrations.medusa.order import sync_sales_order

from .utils import TestCase


class TestOrder(TestCase):
	def _sync(self):
		order = self.load_fixture("order")
		sync_sales_order(order)
		return order

	def _get_so(self):
		name = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: "order_01HORDER00000000000000001"}, "name")
		return frappe.get_doc("Sales Order", name) if name else None

	def test_sync_sales_order_creates_order(self):
		self._sync()

		so = self._get_so()
		self.assertIsNotNone(so)
		self.assertEqual(so.get(ORDER_NUMBER_FIELD), "1042")
		self.assertEqual(so.docstatus, 1)

	def test_order_items_and_customer(self):
		self._sync()
		so = self._get_so()

		# two line items: mug (qty 2 @ 19.99) and tee variant (qty 1 @ 24.99)
		self.assertEqual(len(so.items), 2)
		by_qty = {item.qty: item.rate for item in so.items}
		self.assertEqual(flt(by_qty[2], 2), 19.99)
		self.assertEqual(flt(by_qty[1], 2), 24.99)

		# the order's customer was synced and attached (not the default customer)
		synced_customer = frappe.db.get_value(
			"Customer", {"medusa_customer_id": "cus_01HCUSTOMER0000000000001"}, "name"
		)
		self.assertTrue(bool(synced_customer))
		self.assertEqual(so.customer, synced_customer)

	def test_order_tax_shipping_and_discount(self):
		self._sync()
		so = self._get_so()

		# taxes: per-line sales tax (3.20 + 2.00) + shipping (7.50)
		total_charges = sum(flt(t.tax_amount) for t in so.taxes)
		self.assertEqual(flt(total_charges, 2), flt(3.20 + 2.00 + 7.50, 2))

		# a shipping charge row is present
		descriptions = [t.description for t in so.taxes]
		self.assertTrue(any("Shipping" in (d or "") for d in descriptions))

		# per-unit discount mapped onto the mug line (4.00 total / qty 2 = 2.00 each)
		mug_line = next(item for item in so.items if item.qty == 2)
		self.assertEqual(flt(mug_line.get("medusa_item_discount"), 2), 2.00)

	def test_sync_is_idempotent(self):
		self._sync()
		first = self._get_so()

		# a second sync of the same order must not create a duplicate Sales Order
		self._sync()
		count = frappe.db.count("Sales Order", {ORDER_ID_FIELD: "order_01HORDER00000000000000001"})
		self.assertEqual(count, 1)

		second = self._get_so()
		self.assertEqual(first.name, second.name)
