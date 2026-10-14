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
	IS_CHARGE_ROW_FIELD,
	ORDER_ITEM_CODE_FIELD,
	RETURN_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.tests.utils import TestCase

CANCELLATION_MODULE = "ecommerce_integrations.unicommerce.cancellation_and_returns"
SYNC_OLD_ORDERS_MODULE = "ecommerce_integrations.unicommerce.sync_old_orders"


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
					posting_date="2026-04-01",
					**{FACILITY_CODE_FIELD: "FAC-001"},
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

	def test_falls_back_to_invoice_facility_code(self):
		"""Old-order sync passes a client but no facility code; the package's
		invoice facility must still reach the Return API helper."""
		mock_client = MagicMock()

		with (
			patch("frappe.db.exists", return_value=False),
			patch(
				"frappe.db.get_value",
				return_value=frappe._dict(
					name="INV-001",
					posting_date="2026-04-01",
					**{FACILITY_CODE_FIELD: "FAC-001"},
				),
			),
			patch(f"{CANCELLATION_MODULE}.create_credit_note"),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log"),
			patch(
				f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1625136326000, {})
			) as get_return_date,
		):
			sync_rto_returns(self.so_data, client=mock_client)

		get_return_date.assert_called_once()
		self.assertEqual(
			get_return_date.call_args.kwargs.get("facility_code"),
			"FAC-001",
			"invoice facility must be used when no facility_code argument is passed",
		)


class TestCustomerInitiatedReturnSync(TestCase):
	"""Test customer-initiated return credit note creation."""

	def setUp(self):
		super().setUp()
		self.so_data = self.load_fixture("order-with-customer-return")
		self.client = MagicMock()

	def test_skips_when_credit_note_already_exists(self):
		"""De-duplication: skip if a credit note with this return code exists."""
		mock_client = MagicMock()
		with (
			patch("frappe.db.exists", return_value=True),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(self.so_data, client=mock_client)

		create_cn.assert_not_called()

	def test_creates_credit_note_when_none_exists(self):
		mock_client = MagicMock()
		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(self.so_data, client=mock_client)

		create_cn.assert_called_once()

	def test_returns_early_for_empty_returns(self):
		"""No-op when the order has no returns."""
		mock_client = MagicMock()
		with patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn:
			sync_customer_initiated_returns({"code": "SO-NO-RETURNS", "returns": []}, client=mock_client)

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

		mock_client = MagicMock()
		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(so_data, client=mock_client)

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
			"INV-001": [
				{"parent": "INV-001", "so_detail": "A", "item_code": "ITEM-A"},
				{"parent": "INV-001", "so_detail": "B", "item_code": "ITEM-B"},
			],
			"INV-002": [
				{"parent": "INV-002", "so_detail": "C", "item_code": "ITEM-C"},
				{"parent": "INV-002", "so_detail": "D", "item_code": "ITEM-D"},
			],
			"INV-003": [
				{"parent": "INV-003", "so_detail": "E", "item_code": "ITEM-E"},
				{"parent": "INV-003", "so_detail": "F", "item_code": "ITEM-F"},
			],
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
			"INV-001": [{"parent": "INV-001", "so_detail": "A", "item_code": "ITEM-A"}],
			"INV-002": [{"parent": "INV-002", "so_detail": "B", "item_code": "ITEM-B"}],
		}

		with patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)):
			self.assertIsNone(_get_invoice_for_return("SO-MULTI", {"A", "B"}))

	def test_finds_invoice_by_item_code_without_row_links(self):
		"""Invoices created without a warehouse allocation have no row links to match.

		Regression test: such returns were always skipped as "no invoice found".
		"""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [{"parent": "INV-001", "so_detail": None, "item_code": "ITEM-A"}],
			"INV-002": [{"parent": "INV-002", "so_detail": None, "item_code": "ITEM-C"}],
		}

		with patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)):
			self.assertEqual(_get_invoice_for_return("SO-MULTI", {"A"}, {"ITEM-C"}), "INV-002")

	def test_row_links_take_precedence_over_item_codes(self):
		"""The same SKU can sit on several invoices, so linked rows decide first."""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [{"parent": "INV-001", "so_detail": None, "item_code": "ITEM-C"}],
			"INV-002": [{"parent": "INV-002", "so_detail": "C", "item_code": "ITEM-C"}],
		}

		with patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)):
			self.assertEqual(_get_invoice_for_return("SO-MULTI", {"C"}, {"ITEM-C"}), "INV-002")

	def test_second_return_of_same_sku_picks_the_unrefunded_invoice(self):
		"""Two link-less invoices can carry the same SKU; the second return must
		not credit the invoice the first return already refunded.

		Regression test: the first matching invoice was always picked, so both
		returns credited it and the other invoice was left with its charges.
		"""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [
				{"parent": "INV-001", "name": "SII-1", "so_detail": None, "item_code": "ITEM-A", "qty": 1}
			],
			"INV-002": [
				{"parent": "INV-002", "name": "SII-2", "so_detail": None, "item_code": "ITEM-A", "qty": 1}
			],
		}
		refunded_by_invoice = {"INV-001": {"SII-1": 1}}

		with (
			patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)),
			patch(
				f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row",
				side_effect=lambda invoice_name, **kwargs: refunded_by_invoice.get(invoice_name, {}),
			) as refunded_lookup,
		):
			self.assertEqual(_get_invoice_for_return("SO-MULTI", set(), {"ITEM-A"}), "INV-002")
			# candidate invoices are compared without locking them
			self.assertTrue(
				refunded_lookup.call_args_list
				and all(c.kwargs.get("for_update") is False for c in refunded_lookup.call_args_list)
			)

	def test_return_skips_when_every_matching_invoice_is_refunded(self):
		"""Nothing left to credit on any matching invoice, so no invoice is picked."""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [
				{"parent": "INV-001", "name": "SII-1", "so_detail": None, "item_code": "ITEM-A", "qty": 1}
			],
			"INV-002": [
				{"parent": "INV-002", "name": "SII-2", "so_detail": None, "item_code": "ITEM-A", "qty": 1}
			],
		}

		with (
			patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)),
			patch(
				f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row",
				return_value={"SII-1": 1, "SII-2": 1},
			),
		):
			self.assertIsNone(_get_invoice_for_return("SO-MULTI", set(), {"ITEM-A"}))

	def test_ambiguous_unrefunded_same_sku_invoices_return_none(self):
		"""Two unrefunded link-less invoices with the same SKU cannot be told apart.

		Regression test: the first one in database order was credited, hitting
		the wrong package's warehouse, batch and charge rows.
		"""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [
				{"parent": "INV-001", "name": "SII-1", "so_detail": None, "item_code": "ITEM-A", "qty": 1}
			],
			"INV-002": [
				{"parent": "INV-002", "name": "SII-2", "so_detail": None, "item_code": "ITEM-A", "qty": 1}
			],
		}

		with (
			patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)),
			patch(f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row", return_value={}),
		):
			self.assertIsNone(_get_invoice_for_return("SO-MULTI", set(), {"ITEM-A"}))


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


class TestCreateCreditNote(TestCase):
	"""create_credit_note rebuilds the tax breakup the v17 return mapper drops."""

	def test_rebuilds_tax_breakup_from_the_invoice(self):
		"""v17 stores the item-wise GST breakup on the invoice; the return mapper drops it.

		Regression test: without rebuilding it, the credit note items carried no
		GST rates and india_compliance rejected the save.
		"""
		from types import SimpleNamespace

		credit_note = SimpleNamespace(
			items=[SimpleNamespace(item_code="ITEM-A", warehouse="Stores - WP")],
			taxes=[SimpleNamespace(tax_amount=-50.0)],
			get=lambda fieldname: None,
		)

		with (
			patch(f"{CANCELLATION_MODULE}.get_return_warehouse", return_value=None),
			patch(f"{CANCELLATION_MODULE}.make_sales_return", return_value=credit_note),
			patch(f"{CANCELLATION_MODULE}.copy_item_wise_tax_details") as copy_details,
		):
			create_credit_note("SI-1")

		copy_details.assert_called_once_with(credit_note, "SI-1", source_doctype="Sales Invoice")


class TestReturnWithChargeItems(TestCase):
	"""Test charge items on an invoice in customer-initiated returns."""

	def _create_credit_note(
		self,
		returned_order_item_codes,
		with_charge_item=True,
		previously_refunded_qty=None,
		si_items=None,
		return_quantities=None,
		charge_items=None,
	):
		"""Return items from an invoice with two products and a charge item; get the partial return mock."""
		from types import SimpleNamespace

		if charge_items is None:
			charge_items = {("cash_on_delivery_charges", 18.0): "COD-CHARGES"}

		so = SimpleNamespace(
			items=[
				frappe._dict(name="SOI-A", item_code="ITEM-A", **{ORDER_ITEM_CODE_FIELD: "A-0"}),
				frappe._dict(name="SOI-B", item_code="ITEM-B", **{ORDER_ITEM_CODE_FIELD: "B-0"}),
			]
		)
		if si_items is None:
			si_items = [
				frappe._dict(name="SII-A", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
				frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			]
			if with_charge_item:
				si_items.append(frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"))

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
			# charge rows are told apart by the configured charge items
			patch(
				"ecommerce_integrations.unicommerce.invoice._get_charge_items",
				return_value=charge_items,
			),
			patch(f"{CANCELLATION_MODULE}._get_invoice_for_return", return_value="SI-1"),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1627900000000, {})),
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
			frappe._dict(name="SII-A1", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-A2", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 2}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A1": 1, "SII-A2": 1})

	def test_split_product_row_is_not_refunded_as_charge(self):
		"""The extra row of a split product must wait for its own return, not ride along with a charge refund."""
		si_items = [
			frappe._dict(name="SII-A1", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-A2", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
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
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=3, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 1}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_return_quantity_spans_split_invoice_rows(self):
		"""Returned units fill the linked invoice rows in order, partly refunding the last one."""
		si_items = [
			frappe._dict(name="SII-A1", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-A2", so_detail="SOI-A", qty=2, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 2}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A1": 1, "SII-A2": 1})

	def test_return_quantity_is_capped_at_not_yet_refunded_units(self):
		"""Units already credited by an earlier credit note are not credited again."""
		si_items = [
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=3, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
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
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=3, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
		]
		handle_partial_returns = self._create_credit_note(
			["A-0"], si_items=si_items, return_quantities={"A-0": 1}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_partial_return_without_row_links_skips_charges(self):
		"""Invoices created without a warehouse allocation have no so_detail on any row.

		Regression test: the completion check treated every such row as a charge,
		so a partial return refunded the whole invoice.
		"""
		si_items = [
			frappe._dict(name="SII-A", so_detail=None, qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail=None, qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
		]
		handle_partial_returns = self._create_credit_note(["A-0"], si_items=si_items)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A": 1})

	def test_full_return_without_row_links_refunds_charge_items(self):
		"""Without row links the returned qty is matched by item code, so completion still works."""
		si_items = [
			frappe._dict(name="SII-A", so_detail=None, qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail=None, qty=1, item_code="ITEM-B"),
			frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
		]
		handle_partial_returns = self._create_credit_note(["A-0", "B-0"], si_items=si_items)

		self.assertFalse(handle_partial_returns.called)

	def test_marked_charge_row_survives_settings_change(self):
		"""A charge row marked at invoicing is still refunded after the charge
		items are reconfigured or charges stop being billed as items.

		Regression test: with the old settings the old charge row counted as a
		product, so the completion check never passed and it was never refunded.
		"""
		si_items = [
			frappe._dict(name="SII-A", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
			frappe._dict(name="SII-B", so_detail="SOI-B", qty=1, item_code="ITEM-B"),
			frappe._dict(
				name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES", **{IS_CHARGE_ROW_FIELD: 1}
			),
		]
		handle_partial_returns = self._create_credit_note(
			["B-0"], si_items=si_items, previously_refunded_qty={"SII-A": 1}, charge_items={}
		)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-B": 1, "SII-COD": 1})

	def test_same_sku_order_rows_credit_distinct_invoice_rows(self):
		"""Two order rows of one SKU credit two invoice rows, not one twice.

		Regression test: without row links both order rows picked the same
		invoice row, and the second assignment overwrote the first, so only one
		unit was credited for a two-unit return.
		"""
		from types import SimpleNamespace

		so = SimpleNamespace(
			items=[
				frappe._dict(name="SOI-A", item_code="ITEM-A", **{ORDER_ITEM_CODE_FIELD: "A-0"}),
				frappe._dict(name="SOI-B", item_code="ITEM-A", **{ORDER_ITEM_CODE_FIELD: "B-0"}),
				frappe._dict(name="SOI-C", item_code="ITEM-C", **{ORDER_ITEM_CODE_FIELD: "C-0"}),
			]
		)
		si = SimpleNamespace(
			name="SI-1",
			get=lambda fieldname: None,
			items=[
				frappe._dict(name="SII-A1", so_detail=None, qty=1, item_code="ITEM-A"),
				frappe._dict(name="SII-A2", so_detail=None, qty=1, item_code="ITEM-A"),
				frappe._dict(name="SII-C", so_detail=None, qty=1, item_code="ITEM-C"),
			],
		)

		with (
			patch("frappe.db.get_value", return_value="SO-1"),
			patch(
				"frappe.get_doc", side_effect=lambda doctype, *a, **k: so if doctype == "Sales Order" else si
			),
			patch("ecommerce_integrations.unicommerce.invoice._get_charge_items", return_value={}),
			patch(f"{CANCELLATION_MODULE}._get_invoice_for_return", return_value="SI-1"),
			patch(f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row", return_value={}),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1627900000000, {})),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log"),
			patch(f"{CANCELLATION_MODULE}.create_credit_note", return_value=MagicMock()),
			patch(f"{CANCELLATION_MODULE}._handle_partial_returns") as handle_partial_returns,
		):
			create_cir_credit_note(
				{"code": "SO-1"},
				{
					"code": "RET-1",
					"returnItems": [
						{"saleOrderItemCode": "A-0", "quantity": 1},
						{"saleOrderItemCode": "B-0", "quantity": 1},
					],
				},
				client=MagicMock(),
			)

		handle_partial_returns.assert_called_once()
		self.assertEqual(handle_partial_returns.call_args.args[1], {"SII-A1": 1, "SII-A2": 1})

	def test_invoice_row_is_locked_against_concurrent_returns(self):
		"""Concurrent returns for one invoice serialize on its row, so charges are refunded exactly once."""
		from types import SimpleNamespace

		so = SimpleNamespace(
			items=[frappe._dict(name="SOI-A", item_code="ITEM-A", **{ORDER_ITEM_CODE_FIELD: "A-0"})]
		)
		si = SimpleNamespace(
			name="SI-1",
			get=lambda fieldname: None,
			items=[
				frappe._dict(name="SII-A", so_detail="SOI-A", qty=1, item_code="ITEM-A"),
				frappe._dict(name="SII-COD", so_detail=None, qty=1, item_code="COD-CHARGES"),
			],
		)
		get_doc = MagicMock(
			side_effect=lambda doctype, *args, **kwargs: so if doctype == "Sales Order" else si
		)

		with (
			patch("frappe.db.get_value", return_value="SO-1"),
			patch("frappe.get_doc", get_doc),
			patch(
				"ecommerce_integrations.unicommerce.invoice._get_charge_items",
				return_value={("cash_on_delivery_charges", 18.0): "COD-CHARGES"},
			),
			patch(f"{CANCELLATION_MODULE}._get_invoice_for_return", return_value="SI-1"),
			patch(f"{CANCELLATION_MODULE}._get_refunded_qty_by_invoice_row", return_value={}),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1627900000000, {})),
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

	def test_skips_credit_note_when_return_date_is_missing(self):
		"""No date, no credit note: the saved dedupe key would block a corrected retry."""
		from types import SimpleNamespace

		so = SimpleNamespace(items=[frappe._dict(name="SOI-A", **{ORDER_ITEM_CODE_FIELD: "A-0"})])
		si = SimpleNamespace(
			name="SI-1",
			get=lambda fieldname: None,
			items=[frappe._dict(name="SII-A", so_detail="SOI-A", qty=1)],
		)

		with (
			patch("frappe.db.get_value", return_value="SO-1"),
			patch(
				"frappe.get_doc", side_effect=lambda doctype, *a, **k: so if doctype == "Sales Order" else si
			),
			patch(f"{CANCELLATION_MODULE}._get_invoice_for_return", return_value="SI-1"),
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(None, None)),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
		):
			create_cir_credit_note(
				{"code": "SO-1"},
				{"code": "RET-1", "returnItems": [{"saleOrderItemCode": "A-0", "quantity": 1}]},
				client=MagicMock(),
			)

		create_cn.assert_not_called()
		self.assertEqual(log.call_args.kwargs["status"], "Invalid")


class TestPartialReturns(TestCase):
	"""Test _handle_partial_returns strips items and rescales tax."""

	@staticmethod
	def _credit_note(items, tax_amount=-72.0, with_tax_rows=True):
		"""v17-shaped credit note: the breakup is parent-level rows, one per item."""
		from types import SimpleNamespace

		tax = SimpleNamespace(tax_amount=tax_amount)
		credit_note = SimpleNamespace(
			items=[SimpleNamespace(**item) for item in items],
			taxes=[tax],
		)
		if with_tax_rows:
			credit_note._item_wise_tax_details = [
				frappe._dict(item=item, tax=tax, rate=18.0, amount=-36.0) for item in credit_note.items
			]
		return credit_note

	def test_strips_non_returned_items_and_rescales_tax(self):
		credit_note = self._credit_note(
			items=[
				{"item_code": "ITEM-A", "qty": -2.0, "rate": 100.0, "sales_invoice_item": "SI-A"},
				{"item_code": "ITEM-B", "qty": -2.0, "rate": 100.0, "sales_invoice_item": "SI-B"},
			]
		)
		row_a, row_b = credit_note._item_wise_tax_details

		_handle_partial_returns(credit_note, {"SI-A": 2})

		self.assertEqual([item.item_code for item in credit_note.items], ["ITEM-A"])
		# ITEM-A fully returned keeps its tax; ITEM-B is dropped with its breakup row
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -36.0)
		self.assertEqual(credit_note._item_wise_tax_details, [row_a])
		self.assertAlmostEqual(row_a.amount, -36.0)

	def test_scales_tax_by_returned_quantity(self):
		"""Returning half the qty of an item credits half its tax."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -2.0, "rate": 100.0, "sales_invoice_item": "SI-A"}]
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -18.0)
		self.assertAlmostEqual(credit_note._item_wise_tax_details[0].amount, -18.0)

	def test_reduces_quantity_of_partially_returned_row(self):
		"""One returned unit of a two-unit row credits one unit, amount and tax included."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -2.0, "rate": 100.0, "sales_invoice_item": "SI-A"}]
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		# the credit note's negative sign is kept on the reduced row
		self.assertAlmostEqual(credit_note.items[0].qty, -1.0)
		self.assertAlmostEqual(credit_note.items[0].amount, -100.0)
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -18.0)

	def test_partial_return_scales_taxable_amount(self):
		"""The breakup's taxable value follows the returned qty, not the billed qty.

		Regression test: a partial return kept the invoice's full taxable
		amount while crediting only some of its units.
		"""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -2.0, "rate": 100.0, "sales_invoice_item": "SI-A"}]
		)
		credit_note._item_wise_tax_details[0].taxable_amount = 200.0

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertAlmostEqual(credit_note._item_wise_tax_details[0].taxable_amount, 100.0)
		self.assertAlmostEqual(credit_note._item_wise_tax_details[0].amount, -18.0)

	def test_breakup_of_only_absent_items_credits_no_tax(self):
		"""Prevent crash when tax breakup references items absent from credit note."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -1.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			with_tax_rows=False,
		)
		credit_note._item_wise_tax_details = [
			frappe._dict(
				item=frappe._dict(item_code="ITEM-B"), tax=credit_note.taxes[0], rate=18.0, amount=-18.0
			)
		]

		_handle_partial_returns(credit_note, {"SI-A": 1})

		# no matching items, so no tax and no rows survive
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 0.0)
		self.assertEqual(credit_note._item_wise_tax_details, [])

	def test_skips_tax_rows_without_a_breakup(self):
		"""Tax rows without breakup rows are left alone."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -1.0, "rate": 100.0, "sales_invoice_item": "SI-A"}],
			tax_amount=50.0,
			with_tax_rows=False,
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertEqual(credit_note.taxes[0].tax_amount, 50.0)

	def test_no_breakup_scales_tax_by_returned_amount(self):
		"""Invoices kept before per-row tax breakup credit tax by the returned share.

		Regression test: their credit notes kept the full tax while only some
		items were returned.
		"""
		credit_note = self._credit_note(
			items=[
				{
					"item_code": "ITEM-A",
					"qty": -2.0,
					"rate": 100.0,
					"amount": -200.0,
					"sales_invoice_item": "SI-A",
				},
				{
					"item_code": "ITEM-B",
					"qty": -1.0,
					"rate": 100.0,
					"amount": -100.0,
					"sales_invoice_item": "SI-B",
				},
			],
			tax_amount=-45.0,
			with_tax_rows=False,
		)

		_handle_partial_returns(credit_note, {"SI-A": 2})

		# ITEM-A is 200 of the 300 net amount, so two thirds of the tax are credited
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -30.0)

	def test_no_breakup_splits_tax_by_tax_account(self):
		"""Items under different tax accounts credit only their own account's tax.

		Regression test: every tax row was scaled by the returned share of the
		whole invoice, so returning the 5% item also credited 18% tax.
		"""
		from types import SimpleNamespace

		def taxed_item(item_code, invoice_item, account):
			return SimpleNamespace(
				item_code=item_code,
				qty=-1.0,
				rate=100.0,
				amount=-100.0,
				sales_invoice_item=invoice_item,
				item_tax_rate=json.dumps({account: 1.0}),
			)

		credit_note = SimpleNamespace(
			items=[
				taxed_item("ITEM-A", "SI-A", "Output GST 5% - _TC"),
				taxed_item("ITEM-B", "SI-B", "Output GST 18% - _TC"),
			],
			taxes=[
				SimpleNamespace(tax_amount=-5.0, account_head="Output GST 5% - _TC"),
				SimpleNamespace(tax_amount=-18.0, account_head="Output GST 18% - _TC"),
			],
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		# ITEM-A returned: its account's tax in full, the other account untouched
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -5.0)
		self.assertAlmostEqual(credit_note.taxes[1].tax_amount, 0.0)

	def test_no_breakup_scales_one_account_by_its_own_returned_share(self):
		"""Within one account, only that account's returned items set the share."""
		from types import SimpleNamespace

		credit_note = SimpleNamespace(
			items=[
				SimpleNamespace(
					item_code="ITEM-A",
					qty=-1.0,
					rate=100.0,
					amount=-100.0,
					sales_invoice_item="SI-A",
					item_tax_rate=json.dumps({"Output GST 18% - _TC": 18.0}),
				),
				SimpleNamespace(
					item_code="ITEM-B",
					qty=-1.0,
					rate=100.0,
					amount=-100.0,
					sales_invoice_item="SI-B",
				),
			],
			taxes=[SimpleNamespace(tax_amount=-18.0, account_head="Output GST 18% - _TC")],
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		# ITEM-A is all of the account's amount, so its full tax is credited
		# even though it is only half of the invoice
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -18.0)

	def test_no_breakup_partial_qty_row_scales_tax_by_billed_amount(self):
		"""A half-returned row credits half its account's tax, not all of it.

		Regression test: the account's share was computed after the row's
		amount was reduced in place, so the denominator shrank with it and
		any partial row credited its account's full tax.
		"""
		from types import SimpleNamespace

		credit_note = SimpleNamespace(
			items=[
				SimpleNamespace(
					item_code="ITEM-A",
					qty=-2.0,
					rate=100.0,
					amount=-200.0,
					sales_invoice_item="SI-A",
					item_tax_rate=json.dumps({"Output GST 18% - _TC": 18.0}),
				)
			],
			taxes=[SimpleNamespace(tax_amount=-36.0, account_head="Output GST 18% - _TC")],
		)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertAlmostEqual(credit_note.items[0].amount, -100.0)
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -18.0)

	def test_drops_parent_level_rows_of_absent_items(self):
		"""The breakup can name an item that isn't on the credit note at all."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": -1.0, "rate": 100.0, "sales_invoice_item": "SI-A"}]
		)
		ghost = frappe._dict(
			item=frappe._dict(item_code="ITEM-GHOST"), tax=credit_note.taxes[0], rate=18.0, amount=-18.0
		)
		credit_note._item_wise_tax_details.append(ghost)

		_handle_partial_returns(credit_note, {"SI-A": 1})

		self.assertNotIn(ghost, credit_note._item_wise_tax_details)
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -36.0)

	def test_returned_row_keeps_only_its_own_tax(self):
		"""Split rows of one item at different rates refund only the returned row's tax."""
		credit_note = self._credit_note(
			items=[
				{"item_code": "ITEM-A", "qty": -1.0, "rate": 100.0, "sales_invoice_item": "SI-A1"},
				{"item_code": "ITEM-A", "qty": -1.0, "rate": 200.0, "sales_invoice_item": "SI-A2"},
			]
		)
		row_a1, row_a2 = credit_note._item_wise_tax_details
		row_a1.amount = -18.0  # tax on the 100 line
		row_a2.amount = -36.0  # tax on the 200 line

		_handle_partial_returns(credit_note, {"SI-A1": 1})

		self.assertEqual([item.sales_invoice_item for item in credit_note.items], ["SI-A1"])
		self.assertEqual(credit_note._item_wise_tax_details, [row_a1])
		self.assertAlmostEqual(row_a1.amount, -18.0)
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, -18.0)


class TestSyncOldReturns(TestCase):
	"""Test _sync_old_returns orchestrates both return types."""

	def test_calls_both_sync_functions(self):
		from ecommerce_integrations.unicommerce.sync_old_orders import _sync_old_returns

		detail = {"code": "SO-OLD-001", "returns": []}

		with (
			patch(f"{SYNC_OLD_ORDERS_MODULE}.sync_customer_initiated_returns") as sync_cir,
			patch(f"{SYNC_OLD_ORDERS_MODULE}.sync_rto_returns") as sync_rto,
		):
			_sync_old_returns(detail, "SO-OLD-001")

		sync_cir.assert_called_once_with(detail, client=None)
		sync_rto.assert_called_once_with(detail, client=None)

	def test_continues_on_customer_return_failure(self):
		"""RTO sync still runs when the customer return sync raises."""
		from ecommerce_integrations.unicommerce.sync_old_orders import _sync_old_returns

		detail = {"code": "SO-OLD-002", "returns": []}

		with (
			patch(
				f"{SYNC_OLD_ORDERS_MODULE}.sync_customer_initiated_returns",
				side_effect=ValueError("Customer return sync failed"),
			),
			patch(f"{SYNC_OLD_ORDERS_MODULE}.sync_rto_returns") as sync_rto,
			patch(f"{SYNC_OLD_ORDERS_MODULE}.create_unicommerce_log") as log,
		):
			_sync_old_returns(detail, "SO-OLD-002")

		sync_rto.assert_called_once()
		log.assert_called_once()
		self.assertEqual(log.call_args.kwargs["status"], "Error")

	def test_continues_on_rto_failure(self):
		"""Customer return sync still runs when the RTO sync raises."""
		from ecommerce_integrations.unicommerce.sync_old_orders import _sync_old_returns

		detail = {"code": "SO-OLD-003", "returns": []}

		with (
			patch(
				f"{SYNC_OLD_ORDERS_MODULE}.sync_rto_returns",
				side_effect=ValueError("RTO sync failed"),
			),
			patch(f"{SYNC_OLD_ORDERS_MODULE}.sync_customer_initiated_returns") as sync_cir,
			patch(f"{SYNC_OLD_ORDERS_MODULE}.create_unicommerce_log") as log,
		):
			_sync_old_returns(detail, "SO-OLD-003")

		sync_cir.assert_called_once()
		log.assert_called_once()
		self.assertEqual(log.call_args.kwargs["status"], "Error")


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
