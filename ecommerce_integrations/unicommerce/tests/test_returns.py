"""Tests for Unicommerce return and credit note functionality."""

import json
from unittest.mock import MagicMock, patch

import frappe

from ecommerce_integrations.unicommerce.cancellation_and_returns import (
	_get_invoice_for_return,
	_get_refunded_qty_by_invoice_row,
	_handle_partial_returns,
	create_cir_credit_note,
	create_credit_note,
	create_rto_return,
	get_return_date_from_package,
	sync_customer_initiated_returns,
	sync_rto_returns,
)
from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	ORDER_ITEM_CODE_FIELD,
	RETURN_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.tests.utils import TestCase

CANCELLATION_MODULE = "ecommerce_integrations.unicommerce.cancellation_and_returns"


class TestRTOReturnSync(TestCase):
	"""Test RTO return credit note creation."""

	def setUp(self):
		super().setUp()
		self.so_data = self.load_fixture("order-with-rto-return")
		self.client = MagicMock()

	def test_skips_when_credit_note_already_exists(self):
		"""De-duplication: skip if a credit note exists for the package."""
		with (
			patch("frappe.db.exists", return_value=True),
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
		):
			sync_rto_returns(self.so_data, client=self.client)

		create_cn.assert_not_called()

	def test_creates_rto_credit_note_for_valid_return(self):
		"""Create credit note when valid RTO return exists and no credit note yet."""
		mock_invoice = {
			"name": "INV-001",
			"posting_date": "2024-08-01",
			"unicommerce_facility_code": "Test-123",
		}

		def mock_db_get_value(*args, **kwargs):
			"""Mock that returns invoice for any Sales Invoice get_value call."""

			# Return mock_invoice as an object that supports attribute access
			# Create a simple object that has both dict and attribute access
			class InvoiceDict(dict):
				def __getattr__(self, name):
					return self[name]

			return InvoiceDict(**mock_invoice)

		with (
			patch("frappe.db.exists", return_value=False),
			patch("frappe.db.get_value", side_effect=mock_db_get_value),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1627900000000, {})),
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log"),
		):
			credit_note_mock = MagicMock()
			credit_note_mock.save = MagicMock()
			create_cn.return_value = credit_note_mock

			sync_rto_returns(self.so_data, client=self.client)

		create_cn.assert_called_once()
		self.assertEqual(create_cn.call_args[0][0], "INV-001")

	def test_skips_when_no_invoice_found(self):
		"""Log and skip the return when the original invoice doesn't exist."""
		with (
			patch("frappe.db.exists", return_value=False),
			patch("frappe.db.get_value", return_value=None),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
		):
			sync_rto_returns(self.so_data, client=self.client)

		log.assert_called_once()
		self.assertEqual(log.call_args.kwargs["status"], "Invalid")
		create_cn.assert_not_called()

	def test_filters_non_rto_returns(self):
		"""Only "Courier Returned" is an RTO; other return types are left alone."""
		so_data = {
			"code": "SO-MIXED-001",
			"returns": [
				{"code": "RET-001", "type": "Customer Returned"},
				{"code": "RET-002", "type": "Some Other Type"},
				{"code": "PKG-RTO", "type": "Courier Returned"},
			],
		}

		checked_packages = []

		def mock_exists(doctype, filters=None, *args, **kwargs):
			checked_packages.append(filters.get(SHIPPING_PACKAGE_CODE_FIELD))
			return False

		with (
			patch("frappe.db.exists", side_effect=mock_exists),
			patch("frappe.db.get_value", return_value=None),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log"),
		):
			sync_rto_returns(so_data, client=self.client)

		self.assertEqual(checked_packages, ["PKG-RTO"])

	def test_error_isolation_continues_to_next_package(self):
		"""One failing package must not skip the rest, eg. a closed accounting period."""
		so_data = {
			"code": "SO-MULTI-RTO",
			"returns": [
				{"code": "PKG-A", "type": "Courier Returned"},
				{"code": "PKG-B", "type": "Courier Returned"},
			],
		}

		attempted = []

		def mock_create_credit_note(invoice_name, posting_date=None):
			attempted.append(invoice_name)
			raise frappe.ValidationError("Accounting period is closed")

		mock_client = MagicMock()

		with (
			patch("frappe.db.exists", return_value=False),
			patch(
				"frappe.db.get_value",
				return_value=frappe._dict(
					name="INV-001",
					posting_date="2024-08-01",
					unicommerce_facility_code="Test-123",
				),
			),
			patch(f"{CANCELLATION_MODULE}.create_credit_note", side_effect=mock_create_credit_note),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1627900000000, {})),
		):
			sync_rto_returns(so_data, client=mock_client)

		# both attempted despite the first raising
		self.assertEqual(len(attempted), 2)
		self.assertEqual(log.call_count, 2)


class TestCustomerInitiatedReturnSync(TestCase):
	"""Test customer-initiated return credit note creation."""

	def setUp(self):
		super().setUp()
		self.so_data = self.load_fixture("order-with-customer-return")
		self.client = MagicMock()

	def test_skips_when_credit_note_already_exists(self):
		"""De-duplication: skip if a credit note with this return code exists."""
		with (
			patch("frappe.db.exists", return_value=True),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(self.so_data, client=self.client)

		create_cn.assert_not_called()

	def test_creates_credit_note_when_none_exists(self):
		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(self.so_data, client=self.client)

		create_cn.assert_called_once()

	def test_returns_early_for_empty_returns(self):
		"""No-op when the order has no returns."""
		with patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn:
			sync_customer_initiated_returns({"code": "SO-NO-RETURNS", "returns": []}, client=self.client)

		create_cn.assert_not_called()

	def test_filters_non_customer_returns(self):
		"""Only "Customer Returned" is handled here; RTO is a separate path."""
		so_data = {
			"code": "SO-MIXED-001",
			"returns": [
				{"code": "RET-001", "type": "Courier Returned"},
				{"code": "RET-002", "type": "Customer Returned"},
			],
		}

		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(so_data, client=self.client)

		create_cn.assert_called_once()

	def test_error_isolation_continues_to_next_return(self):
		"""One failing return must not skip the rest of the order's returns."""
		so_data = {
			"code": "SO-MULTI-CIR",
			"returns": [
				{"code": "RET-A", "type": "Customer Returned"},
				{"code": "RET-B", "type": "Customer Returned"},
			],
		}

		attempted = []

		def mock_create(so_data, return_data, client=None):
			attempted.append(return_data["code"])
			raise frappe.ValidationError("Accounting period is closed")

		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note", side_effect=mock_create),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
		):
			sync_customer_initiated_returns(so_data, client=self.client)

		self.assertEqual(attempted, ["RET-A", "RET-B"])
		self.assertEqual(log.call_count, 2)


class TestInvoiceForReturnSelection(TestCase):
	"""Test _get_invoice_for_return finds the right invoice for multi-package orders."""

	@staticmethod
	def _mock_get_all(invoices, invoice_item_map):
		"""Stub frappe.get_all for the two queries _get_invoice_for_return makes."""

		def mock_get_all(doctype, **kwargs):
			if doctype == "Sales Invoice":
				return invoices
			if doctype == "Sales Invoice Item":
				parents = kwargs["filters"]["parent"][1]
				return [item for parent in parents for item in invoice_item_map.get(parent, [])]
			return []

		return mock_get_all

	def test_returns_none_when_no_invoices(self):
		with patch("frappe.get_all", return_value=[]):
			self.assertIsNone(_get_invoice_for_return("SO-NO-INV", {"A"}))

	def test_returns_single_invoice_without_checking_items(self):
		"""With one invoice there's nothing to choose between, so skip the item query."""
		with patch("frappe.get_all", return_value=["INV-001"]) as get_all:
			self.assertEqual(_get_invoice_for_return("SO-001", {"A"}), "INV-001")

		self.assertEqual(get_all.call_count, 1)

	def test_finds_invoice_containing_returned_items(self):
		invoices = ["INV-001", "INV-002", "INV-003"]
		invoice_item_map = {
			"INV-001": [{"parent": "INV-001", "so_detail": "A"}, {"parent": "INV-001", "so_detail": "B"}],
			"INV-002": [{"parent": "INV-002", "so_detail": "C"}, {"parent": "INV-002", "so_detail": "D"}],
			"INV-003": [{"parent": "INV-003", "so_detail": "E"}, {"parent": "INV-003", "so_detail": "F"}],
		}

		with patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)):
			self.assertEqual(_get_invoice_for_return("SO-MULTI", {"A", "B"}), "INV-001")
			self.assertEqual(_get_invoice_for_return("SO-MULTI", {"C", "D"}), "INV-002")
			# a subset of one invoice's rows still resolves to that invoice
			self.assertEqual(_get_invoice_for_return("SO-MULTI", {"E"}), "INV-003")

	def test_returns_none_when_items_spread_across_invoices(self):
		"""No single invoice covers the return, so there's nothing safe to return against."""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [{"parent": "INV-001", "so_detail": "A"}],
			"INV-002": [{"parent": "INV-002", "so_detail": "B"}],
		}

		with patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)):
			self.assertIsNone(_get_invoice_for_return("SO-MULTI", {"A", "B"}))


class TestRefundedQtyLookup(TestCase):
	"""The refund lookup aggregates in SQL and locks rows only where the database allows it."""

	def test_aggregates_and_locks_rows_on_mariadb(self):
		with (
			patch.object(frappe.db, "db_type", "mariadb"),
			patch("frappe.db.sql", return_value=[]) as run_sql,
		):
			self.assertEqual(_get_refunded_qty_by_invoice_row("SI-1"), {})

		query = run_sql.call_args.args[0]
		self.assertIn("FOR UPDATE", query)
		self.assertIn("GROUP BY", query)
		self.assertIn("SUM", query)

	def test_aggregates_without_row_lock_on_postgres(self):
		"""Postgres rejects FOR UPDATE combined with GROUP BY.

		Regression test: the locking aggregate errored on Postgres sites,
		so no further credit note could be created for an invoice.
		"""
		with (
			patch.object(frappe.db, "db_type", "postgres"),
			patch("frappe.db.sql", return_value=[]) as run_sql,
		):
			self.assertEqual(_get_refunded_qty_by_invoice_row("SI-1"), {})

		self.assertNotIn("FOR UPDATE", run_sql.call_args.args[0])
		self.assertIn("GROUP BY", run_sql.call_args.args[0])


class TestReturnWithChargeItems(TestCase):
	"""Test charge items on an invoice in customer-initiated returns."""

	def _create_credit_note(
		self,
		returned_order_item_codes,
		with_charge_item=True,
		previously_refunded_qty=None,
		si_items=None,
		return_quantities=None,
	):
		"""Return items from an invoice with two products and a charge item; get the partial return mock."""
		from types import SimpleNamespace

		so = SimpleNamespace(
			items=[
				frappe._dict(name="SOI-A", **{ORDER_ITEM_CODE_FIELD: "A-0"}),
				frappe._dict(name="SOI-B", **{ORDER_ITEM_CODE_FIELD: "B-0"}),
			]
		)
		if si_items is None:
			si_items = [
				frappe._dict(name="SII-A", so_detail="SOI-A", qty=1),
				frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			]
			if with_charge_item:
				si_items.append(frappe._dict(name="SII-COD", so_detail=None, qty=1))

		si = SimpleNamespace(name="SI-1", get=lambda fieldname: None, items=si_items)
		quantities = return_quantities or {}
		return_data = {
			"code": "RET-1",
			"returnItems": [
				{"saleOrderItemCode": code, "quantity": quantities.get(code, 1)}
				for code in returned_order_item_codes
			],
		}

		with (
			patch("frappe.db.get_value", return_value="SO-1"),
			patch(
				"frappe.get_doc",
				side_effect=lambda doctype, *args, **kwargs: so if doctype == "Sales Order" else si,
			),
			patch(f"{CANCELLATION_MODULE}._get_invoice_for_return", return_value="SI-1"),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(None, {})),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log"),
			patch(f"{CANCELLATION_MODULE}.create_credit_note", return_value=MagicMock()),
			patch(
				f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row",
				return_value=dict(previously_refunded_qty or {}),
			),
			patch(f"{CANCELLATION_MODULE}._handle_partial_returns") as handle_partial_returns,
		):
			create_cir_credit_note({"code": "SO-1"}, return_data, client=MagicMock())

		return handle_partial_returns

	def test_full_return_is_not_partial(self):
		"""Returning every product reverses the whole invoice, charge items included."""
		self.assertFalse(self._create_credit_note(["A-0", "B-0"]).called)

	def test_partial_return(self):
		"""Returning some products still removes the rest."""
		handle_partial_returns = self._create_credit_note(["A-0"])

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_last_partial_return_refunds_charge_items(self):
		"""The return completing all product rows refunds charges exactly once."""
		handle_partial_returns = self._create_credit_note(["B-0"], previously_refunded_qty={"SII-A": 1})

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-B": 1, "SII-COD": 1})

	def test_last_partial_return_does_not_refund_charge_twice(self):
		"""A charge already present on a credit note is not included again."""
		handle_partial_returns = self._create_credit_note(
			["B-0"], previously_refunded_qty={"SII-A": 1, "SII-COD": 1}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-B": 1})

	def test_full_return_without_charge_items_is_not_partial(self):
		"""An invoice with no charge item is unaffected: every row has a sales order row."""
		self.assertFalse(self._create_credit_note(["A-0", "B-0"], with_charge_item=False).called)

	def test_partial_return_without_charge_items(self):
		"""An invoice with no charge item still drops the rows that weren't returned."""
		handle_partial_returns = self._create_credit_note(["A-0"], with_charge_item=False)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_return_of_split_product_rows_credits_every_row(self):
		"""A sales order row billed as two invoice rows is refunded on both rows.

		Regression test: the so_detail-keyed map used to keep only the last
		invoice row of a split sales order row, silently dropping the rest.
		"""
		si_items = [
			frappe._dict(name="SII-A1", so_detail="SOI-A", qty=1),
			frappe._dict(name="SII-A2", so_detail="SOI-A", qty=1),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			frappe._dict(name="SII-COD", so_detail=None, qty=1),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 2}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A1": 1, "SII-A2": 1})

	def test_split_product_row_is_not_refunded_as_charge(self):
		"""The extra row of a split product must wait for its own return, not ride along with a charge refund."""
		si_items = [
			frappe._dict(name="SII-A1", so_detail="SOI-A", qty=1),
			frappe._dict(name="SII-A2", so_detail="SOI-A", qty=1),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			frappe._dict(name="SII-COD", so_detail=None, qty=1),
		]
		# returning the other product, with the split rows returned previously,
		# refunds the charge once the last product row is credited
		handle_partial_returns = self._create_credit_note(
			["B-0"], si_items=si_items, previously_refunded_qty={"SII-A1": 1, "SII-A2": 1}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-B": 1, "SII-COD": 1})

	def test_partial_quantity_return_credits_returned_quantity_only(self):
		"""Returning one unit of a three-unit row refunds one unit, not the whole row."""
		si_items = [
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=3),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			frappe._dict(name="SII-COD", so_detail=None, qty=1),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 1}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_return_quantity_spans_split_invoice_rows(self):
		"""Returned units fill the linked invoice rows in order, partly refunding the last one."""
		si_items = [
			frappe._dict(name="SII-A1", so_detail="SOI-A", qty=1),
			frappe._dict(name="SII-A2", so_detail="SOI-A", qty=2),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			frappe._dict(name="SII-COD", so_detail=None, qty=1),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 2}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A1": 1, "SII-A2": 1})

	def test_return_quantity_is_capped_at_not_yet_refunded_units(self):
		"""Units already credited by an earlier credit note are not credited again."""
		si_items = [
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=3),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			frappe._dict(name="SII-COD", so_detail=None, qty=1),
		]
		# one unit of A and all of B already refunded: a return of five more
		# units of A credits only the two remaining ones, which completes the
		# invoice and refunds the charge
		handle_partial_returns = self._create_credit_note(
			["A-0"],
			si_items=si_items,
			return_quantities={"A-0": 5},
			previously_refunded_qty={"SII-A": 1, "SII-B": 1},
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 2, "SII-COD": 1})

	def test_partial_quantity_return_does_not_refund_charges(self):
		"""Charges wait until every unit of every product row is cumulatively returned."""
		si_items = [
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=3),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1),
			frappe._dict(name="SII-COD", so_detail=None, qty=1),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 1}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_invoice_row_is_locked_against_concurrent_returns(self):
		"""Concurrent returns for one invoice serialize on its row, so charges are refunded exactly once."""
		from types import SimpleNamespace

		so = SimpleNamespace(items=[frappe._dict(name="SOI-A", **{ORDER_ITEM_CODE_FIELD: "A-0"})])
		si = SimpleNamespace(
			name="SI-1",
			get=lambda fieldname: None,
			items=[
				frappe._dict(name="SII-A", so_detail="SOI-A", qty=1),
				frappe._dict(name="SII-COD", so_detail=None, qty=1),
			],
		)
		get_doc = MagicMock(
			side_effect=lambda doctype, *args, **kwargs: so if doctype == "Sales Order" else si
		)

		with (
			patch("frappe.db.get_value", return_value="SO-1"),
			patch("frappe.get_doc", get_doc),
			patch(f"{CANCELLATION_MODULE}._get_invoice_for_return", return_value="SI-1"),
			patch(f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row", return_value={}),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(None, {})),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log"),
			patch(f"{CANCELLATION_MODULE}.create_credit_note", return_value=MagicMock()),
		):
			create_cir_credit_note(
				{"code": "SO-1"},
				{"code": "RET-1", "returnItems": [{"saleOrderItemCode": "A-0", "quantity": 1}]},
				client=MagicMock(),
			)

		si_call = next(call for call in get_doc.call_args_list if call.args[0] == "Sales Invoice")
		self.assertIs(si_call.kwargs.get("for_update"), True)


class TestPartialReturns(TestCase):
	"""Test _handle_partial_returns strips items and rescales tax."""

	@staticmethod
	def _credit_note(items, item_wise_tax_detail=None, tax_amount=100.0):
		from types import SimpleNamespace

		tax = SimpleNamespace(tax_amount=tax_amount)
		if item_wise_tax_detail is not None:
			tax.item_wise_tax_detail = json.dumps(item_wise_tax_detail)

		return SimpleNamespace(
			items=[SimpleNamespace(**item) for item in items],
			taxes=[tax],
		)

	def test_strips_non_returned_items_and_rescales_tax(self):
		credit_note = self._credit_note(
			items=[
				{"item_code": "ITEM-A", "qty": 2.0, "rate": 100.0, "sales_invoice_item": "SI-A"},
				{"item_code": "ITEM-B", "qty": 2.0, "rate": 100.0, "sales_invoice_item": "SI-B"},
			],
			item_wise_tax_detail={"ITEM-A": [18.0, 36.0], "ITEM-B": [18.0, 36.0]},
		)

		_handle_partial_returns(credit_note, {"SI-A": 2})

		self.assertEqual([item.item_code for item in credit_note.items], ["ITEM-A"])
		# ITEM-A fully returned keeps its tax, ITEM-B is zeroed
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 36.0)

	def test_scales_tax_by_returned_quantity(self):
		"""Returning half the qty of an item credits half its tax."""
		credit_note = self._credit_note(
			items=[
				{"item_code": "ITEM-A", "qty": 1.0, "rate": 100.0, "sales_invoice_item": "SI-A1"},
				{"item_code": "ITEM-A", "qty": 1.0, "rate": 100.0, "sales_invoice_item": "SI-A2"},
			],
			item_wise_tax_detail={"ITEM-A": [18.0, 36.0]},
		)

		_handle_partial_returns(credit_note, {"SI-A1": 1})

		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 18.0)

	def test_reduces_quantity_of_partially_returned_row(self):
		"""One returned unit of a two-unit row credits one unit, amount and tax included."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 2.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail={"ITEM-A": [18.0, 36.0]},
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertAlmostEqual(credit_note.items[0].qty, 1.0)
		self.assertAlmostEqual(credit_note.items[0].amount, 100.0)
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 18.0)

	def test_quantity_reduction_keeps_credit_note_sign(self):
		"""Credit note rows carry negative quantities; a partial refund stays negative."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -2.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail={"ITEM-A": [18.0, 36.0]},
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertAlmostEqual(credit_note.items[0].qty, -1.0)
		self.assertAlmostEqual(credit_note.items[0].amount, -100.0)
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 18.0)

	def test_survives_tax_detail_naming_an_absent_item(self):
		"""The breakup can name an item that isn't on the credit note."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail={"ITEM-A": [18.0, 18.0], "ITEM-GHOST": [18.0, 18.0]},
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		# only the item on the document contributes tax
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 18.0)

	def test_division_by_zero_protection(self):
		"""Prevent crash when tax breakup references items absent from credit note."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail={"ITEM-B": [18.0, 18.0]},  # ITEM-B not in credit note
		)

		# Should not raise division by zero
		_handle_partial_returns(credit_note, {"SI-A": 1})

		# Tax should be zero since no matching items
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 0.0)

	def test_skips_tax_rows_without_item_wise_detail(self):
		"""Tax rows without an item wise breakup are left alone."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail=None,
			tax_amount=50.0,
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertEqual(credit_note.taxes[0].tax_amount, 50.0)


class TestReturnAPIIntegration(TestCase):
	"""Test Return API integration for accurate return dates."""

	def test_get_return_details_from_client(self):
		"""Test that client.get_return_details method is called correctly."""
		mock_client = MagicMock()
		return_details = {
			"returnSaleOrderValue": {"returnCreatedDate": "2024-08-14 10:30:00", "code": "RET-001"}
		}
		mock_client.get_return_details.return_value = return_details

		timestamp, details = get_return_date_from_package(
			client=mock_client, shipment_code="PKG-001", facility_code="Test-123"
		)

		self.assertIsNotNone(timestamp)
		self.assertIsNotNone(details)
		mock_client.get_return_details.assert_called_once_with(
			reverse_pickup_code=None, shipment_code="PKG-001", facility_code="Test-123"
		)

	def test_handles_return_api_failure_gracefully(self):
		"""Test that Return API failures don't crash the system."""
		mock_client = MagicMock()
		mock_client.get_return_details.return_value = None

		timestamp, details = get_return_date_from_package(
			client=mock_client, shipment_code="PKG-001", facility_code="Test-123"
		)

		self.assertIsNone(timestamp)
		self.assertIsNone(details)

	def test_parses_return_date_correctly(self):
		"""Test that return date string is parsed to timestamp correctly."""
		mock_client = MagicMock()
		return_details = {"returnSaleOrderValue": {"returnCreatedDate": "2024-08-14 10:30:00"}}
		mock_client.get_return_details.return_value = return_details

		timestamp, details = get_return_date_from_package(
			client=mock_client, shipment_code="PKG-001", facility_code="Test-123"
		)

		# Should be a valid timestamp (milliseconds)
		self.assertIsInstance(timestamp, int)
		self.assertGreater(timestamp, 1700000000000)  # Sometime after 2023
