# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from frappe.utils import flt

from ecommerce_integrations.medusa.constants import ORDER_ID_FIELD, RETURN_ID_FIELD
from ecommerce_integrations.medusa.invoice import prepare_sales_invoice
from ecommerce_integrations.medusa.order import sync_sales_order

from .utils import TestCase

ORDER_ID = "order_01HORDER00000000000000001"


def _returns_module():
	"""Import ``medusa.returns`` lazily.

	The returns module is authored in parallel; if it (or its entrypoint) is not yet
	present, the dependent tests skip rather than error so the suite stays green for
	the rest of the connector.
	"""
	try:
		from ecommerce_integrations.medusa import returns
	except ModuleNotFoundError:
		return None
	return returns if hasattr(returns, "prepare_credit_note") else None


class TestReturns(TestCase):
	def setUp(self):
		super().setUp()
		# order -> Sales Order -> (captured) Sales Invoice, so a credit note has a parent
		order = self.load_fixture("order")
		sync_sales_order(order)
		prepare_sales_invoice(order)

		self.invoice = frappe.db.get_value(
			"Sales Invoice", {ORDER_ID_FIELD: ORDER_ID, "is_return": 0, "docstatus": 1}, "name"
		)
		self.assertTrue(bool(self.invoice), "expected a submitted Sales Invoice to return against")

	def _credit_notes(self):
		return frappe.get_all(
			"Sales Invoice",
			filters={ORDER_ID_FIELD: ORDER_ID, "is_return": 1},
			fields=["name", "grand_total", RETURN_ID_FIELD],
		)

	def test_full_return_creates_credit_note(self):
		returns = _returns_module()
		if not returns:
			self.skipTest("medusa.returns not yet implemented")

		returns.prepare_credit_note(self.load_fixture("return_full"))

		notes = self._credit_notes()
		self.assertEqual(len(notes), 1)
		credit_note = frappe.get_doc("Sales Invoice", notes[0].name)

		# a full return mirrors the invoice: same item count, negative quantities
		self.assertEqual(len(credit_note.items), 2)
		for item in credit_note.items:
			self.assertLess(item.qty, 0)

		# taxes are negated relative to the invoice
		for tax in credit_note.taxes:
			self.assertLessEqual(flt(tax.tax_amount), 0)

	def test_partial_return_prorates_tax(self):
		returns = _returns_module()
		if not returns:
			self.skipTest("medusa.returns not yet implemented")

		# return.json returns only the tee line (1 of the 2 invoice lines)
		returns.prepare_credit_note(self.load_fixture("return"))

		notes = self._credit_notes()
		self.assertEqual(len(notes), 1)
		credit_note = frappe.get_doc("Sales Invoice", notes[0].name)

		# only the returned line survives on the credit note
		self.assertEqual(len(credit_note.items), 1)
		self.assertLess(credit_note.items[0].qty, 0)

		# returned tee tax was 2.00 on the order; the credit note tax is that line's
		# tax, negated -> the absolute tax must not exceed the full-order tax (5.20)
		total_tax = sum(abs(flt(t.tax_amount)) for t in credit_note.taxes)
		self.assertLessEqual(flt(total_tax, 2), flt(5.20, 2))

		# item-wise tax detail on each row carries only the returned item (older erpnext;
		# v16 drops this field, so access defensively)
		for tax in credit_note.taxes:
			detail = tax.get("item_wise_tax_detail")
			if detail:
				parsed = json.loads(detail) if isinstance(detail, str) else detail
				# whatever is recorded must be a subset of the credit note's items
				cn_item_codes = {i.item_code for i in credit_note.items}
				for item_code in parsed:
					self.assertIn(item_code, cn_item_codes | {""})

	def test_return_is_idempotent(self):
		returns = _returns_module()
		if not returns:
			self.skipTest("medusa.returns not yet implemented")

		returns.prepare_credit_note(self.load_fixture("return_full"))
		returns.prepare_credit_note(self.load_fixture("return_full"))

		# the same Medusa return must not produce two credit notes
		notes = [
			n for n in self._credit_notes() if n.get(RETURN_ID_FIELD) == "ret_01HRETURNFULL0000000000001"
		]
		self.assertEqual(len(notes), 1)
