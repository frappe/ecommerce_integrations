# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import copy

import frappe

from ecommerce_integrations.medusa.constants import ORDER_ID_FIELD
from ecommerce_integrations.medusa.invoice import prepare_sales_invoice

from .utils import TestCase

ORDER_ID = "order_01HORDER00000000000000001"


class TestInvoice(TestCase):
	def _submitted_si(self):
		return frappe.db.exists("Sales Invoice", {ORDER_ID_FIELD: ORDER_ID, "is_return": 0, "docstatus": 1})

	def test_invoice_requires_captured_payment(self):
		# an order.completed with no captured payment must not be recorded as paid
		order = copy.deepcopy(self.load_fixture("order"))
		order["payment_collections"] = [{"id": "pc_1", "status": "authorized", "payments": []}]

		from ecommerce_integrations.medusa.order import sync_sales_order

		sync_sales_order(order)
		prepare_sales_invoice(order)
		self.assertFalse(self._submitted_si())

	def test_invoice_ensures_sales_order_when_out_of_order(self):
		# order.completed processed before order.placed: no Sales Order exists yet, but the
		# captured order must still produce both the Sales Order and the Sales Invoice.
		order = self.load_fixture("order")
		self.assertFalse(frappe.db.exists("Sales Order", {ORDER_ID_FIELD: ORDER_ID}))

		prepare_sales_invoice(order)

		self.assertTrue(frappe.db.exists("Sales Order", {ORDER_ID_FIELD: ORDER_ID}))
		self.assertTrue(self._submitted_si())
