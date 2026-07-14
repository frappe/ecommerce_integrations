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
		self.assertEqual(flt(by_qty[2], 2), 17.99)  # net of the per-unit discount (19.99 - 2.00)
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

		# the discount reduces the order total (Medusa order.total = 73.67) rather than
		# being left as an informational field that would overstate the customer balance
		self.assertEqual(flt(so.grand_total, 2), 73.67)

	def test_sync_is_idempotent(self):
		self._sync()
		first = self._get_so()

		# a second sync of the same order must not create a duplicate Sales Order
		self._sync()
		count = frappe.db.count("Sales Order", {ORDER_ID_FIELD: "order_01HORDER00000000000000001"})
		self.assertEqual(count, 1)

		second = self._get_so()
		self.assertEqual(first.name, second.name)

	def test_backfill_replays_paid_and_fulfilled_state(self):
		from ecommerce_integrations.medusa.order import sync_old_orders

		setting = frappe.get_doc("Medusa Setting")
		setting.db_set("sync_old_orders", 1)
		setting.db_set("old_orders_from", "2024-01-01 00:00:00")
		setting.db_set("old_orders_to", "2024-12-31 00:00:00")

		sync_old_orders()

		# the historical order is paid + fulfilled, so backfill must replay SO + SI + DN
		# rather than leaving a bare open Sales Order
		self.assertIsNotNone(self._get_so())
		self.assertTrue(
			frappe.db.exists(
				"Sales Invoice", {ORDER_ID_FIELD: "order_01HORDER00000000000000001", "is_return": 0}
			)
		)
		self.assertTrue(
			frappe.db.exists("Delivery Note", {ORDER_ID_FIELD: "order_01HORDER00000000000000001"})
		)
		# returns already made on the historical order are replayed as credit notes too
		self.assertTrue(
			frappe.db.exists(
				"Sales Invoice", {ORDER_ID_FIELD: "order_01HORDER00000000000000001", "is_return": 1}
			)
		)

	def test_ensure_submits_existing_draft_sales_order(self):
		# a leftover draft Sales Order (failed prior run / manual import) must not block
		# replay: ensure_sales_order should submit it rather than skip on the dedup.
		from ecommerce_integrations.medusa.order import (
			ensure_sales_order,
			get_order_items,
			get_order_taxes,
		)
		from ecommerce_integrations.medusa.product import create_items_if_not_exist
		from ecommerce_integrations.utils.price_list import get_dummy_price_list
		from ecommerce_integrations.utils.taxation import get_dummy_tax_category

		order = self.load_fixture("order")
		create_items_if_not_exist(order)
		setting = frappe.get_doc("Medusa Setting")

		draft = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"naming_series": "SAL-ORD-.YYYY.-",
				ORDER_ID_FIELD: "order_01HORDER00000000000000001",
				"customer": setting.default_customer,
				"company": setting.company,
				"currency": "inr",
				"transaction_date": "2024-01-01",
				"delivery_date": "2024-12-31",
				"selling_price_list": get_dummy_price_list(),
				"ignore_pricing_rule": 1,
				"tax_category": get_dummy_tax_category(),
				"items": get_order_items(order, setting),
				"taxes": get_order_taxes(order, setting),
			}
		)
		draft.flags.ignore_mandatory = True
		draft.insert(ignore_permissions=True)
		self.assertEqual(draft.docstatus, 0)

		so = ensure_sales_order(order)
		self.assertIsNotNone(so)
		self.assertEqual(so.docstatus, 1)
		self.assertEqual(
			frappe.db.count("Sales Order", {ORDER_ID_FIELD: "order_01HORDER00000000000000001"}), 1
		)
