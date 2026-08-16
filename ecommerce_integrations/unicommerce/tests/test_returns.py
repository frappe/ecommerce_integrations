"""Tests for Unicommerce return and credit note functionality."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from ecommerce_integrations.unicommerce.cancellation_and_returns import (
	_get_invoice_for_return,
	_handle_partial_returns,
	sync_customer_initiated_returns,
	sync_rto_returns,
)
from ecommerce_integrations.unicommerce.constants import (
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

	def test_skips_when_credit_note_already_exists(self):
		"""De-duplication: skip if a credit note exists for the package."""
		with (
			patch("frappe.db.exists", return_value=True),
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
		):
			sync_rto_returns(self.so_data)

		create_cn.assert_not_called()

	def test_dedupe_check_ignores_docstatus(self):
		"""The de-dupe lookup must not filter on docstatus.

		Credit notes are left in draft, so a docstatus=1 filter never matches one
		and every run would add another duplicate.
		"""
		seen_filters = []

		def mock_exists(doctype, filters=None, *args, **kwargs):
			seen_filters.append(filters)
			# a draft credit note exists: is_return row present, docstatus still 0
			return bool(filters and filters.get("is_return") == 1)

		with (
			patch("frappe.db.exists", side_effect=mock_exists),
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
		):
			sync_rto_returns(self.so_data)

		dedupe_filters = [
			f
			for f in seen_filters
			if f and f.get("is_return") == 1 and f.get(SHIPPING_PACKAGE_CODE_FIELD) == "PKG-RTO-001"
		]
		self.assertTrue(dedupe_filters, "no de-dupe lookup was made for an existing credit note")
		for filters in dedupe_filters:
			self.assertNotIn("docstatus", filters)

		create_cn.assert_not_called()

	def test_skips_when_no_invoice_found(self):
		"""Log and skip the return when the original invoice doesn't exist."""
		with (
			patch("frappe.db.exists", return_value=False),
			patch("frappe.db.get_value", return_value=None),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
			patch(f"{CANCELLATION_MODULE}.create_credit_note") as create_cn,
		):
			sync_rto_returns(self.so_data)

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
			sync_rto_returns(so_data)

		self.assertEqual(checked_packages, ["PKG-RTO"])

	def test_only_package_filters_to_one_return(self):
		"""create_rto_return passes only_package so a sweep handles just that package."""
		so_data = {
			"code": "SO-MULTI-RTO",
			"returns": [
				{"code": "PKG-A", "type": "Courier Returned"},
				{"code": "PKG-B", "type": "Courier Returned"},
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
			sync_rto_returns(so_data, only_package="PKG-B")

		self.assertEqual(checked_packages, ["PKG-B"])

	def test_continues_to_next_package_on_failure(self):
		"""One failing package must not skip the rest, eg. a closed accounting period."""
		so_data = {
			"code": "SO-MULTI-RTO",
			"returns": [
				{"code": "PKG-A", "type": "Courier Returned", "created": 1625136326000},
				{"code": "PKG-B", "type": "Courier Returned", "created": 1625136326000},
			],
		}

		attempted = []

		def mock_create_credit_note(invoice_name, posting_date=None):
			attempted.append(invoice_name)
			raise frappe.ValidationError("Accounting period is closed")

		# Create mock client to pass the client validation check
		mock_client = MagicMock()

		with (
			patch("frappe.db.exists", return_value=False),
			patch(
				"frappe.db.get_value",
				return_value=frappe._dict(name="INV-001", posting_date="2026-04-01"),
			),
			patch(f"{CANCELLATION_MODULE}.create_credit_note", side_effect=mock_create_credit_note),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
			patch(f"{CANCELLATION_MODULE}.get_return_date_from_package", return_value=(1625136326000, {})),
		):
			sync_rto_returns(so_data, client=mock_client)

		# both attempted despite the first raising
		self.assertEqual(len(attempted), 2)
		self.assertEqual(log.call_count, 2)
		self.assertEqual(log.call_args.kwargs["status"], "Error")


class TestCustomerInitiatedReturnSync(TestCase):
	"""Test customer-initiated return credit note creation."""

	def setUp(self):
		super().setUp()
		self.so_data = self.load_fixture("order-with-customer-return")

	def test_skips_when_credit_note_already_exists(self):
		"""De-duplication: skip if a credit note with this return code exists."""
		with (
			patch("frappe.db.exists", return_value=True),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(self.so_data)

		create_cn.assert_not_called()

	def test_creates_credit_note_when_none_exists(self):
		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn,
		):
			sync_customer_initiated_returns(self.so_data)

		create_cn.assert_called_once()
		self.assertEqual(create_cn.call_args.args[1]["code"], "RET-CIR-001")

	def test_returns_early_for_empty_returns(self):
		"""No-op when the order has no returns."""
		with patch(f"{CANCELLATION_MODULE}.create_cir_credit_note") as create_cn:
			sync_customer_initiated_returns({"code": "SO-NO-RETURNS", "returns": []})

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
			sync_customer_initiated_returns(so_data)

		create_cn.assert_called_once()
		self.assertEqual(create_cn.call_args.args[1]["code"], "RET-002")

	def test_continues_to_next_return_on_failure(self):
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

		# Create mock client to pass to the sync function
		mock_client = MagicMock()

		with (
			patch("frappe.db.exists", return_value=False),
			patch(f"{CANCELLATION_MODULE}.create_cir_credit_note", side_effect=mock_create),
			patch(f"{CANCELLATION_MODULE}.create_unicommerce_log") as log,
		):
			sync_customer_initiated_returns(so_data, client=mock_client)

		self.assertEqual(attempted, ["RET-A", "RET-B"])
		self.assertEqual(log.call_count, 2)
		self.assertEqual(log.call_args.kwargs["status"], "Error")


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

	def test_returns_none_for_empty_returned_items(self):
		"""An empty set is a subset of everything, so it must not match any invoice."""
		invoices = ["INV-001", "INV-002"]
		invoice_item_map = {
			"INV-001": [{"parent": "INV-001", "so_detail": "A"}],
			"INV-002": [{"parent": "INV-002", "so_detail": "B"}],
		}

		with patch("frappe.get_all", side_effect=self._mock_get_all(invoices, invoice_item_map)):
			self.assertIsNone(_get_invoice_for_return("SO-MULTI", set()))


class TestPartialReturns(TestCase):
	"""Test _handle_partial_returns strips items and rescales tax.

	Doubles are SimpleNamespace, not frappe._dict: on a _dict `.items` resolves to
	the built-in dict method, and hasattr() is always True.
	"""

	@staticmethod
	def _credit_note(items, item_wise_tax_detail=None, tax_amount=100.0):
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
				{"item_code": "ITEM-A", "qty": 2.0, "sales_invoice_item": "SI-A"},
				{"item_code": "ITEM-B", "qty": 2.0, "sales_invoice_item": "SI-B"},
			],
			item_wise_tax_detail={"ITEM-A": [18.0, 36.0], "ITEM-B": [18.0, 36.0]},
		)

		_handle_partial_returns(credit_note, ["SI-A"])

		self.assertEqual([item.item_code for item in credit_note.items], ["ITEM-A"])
		# ITEM-A fully returned keeps its tax, ITEM-B is zeroed
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 36.0)

	def test_scales_tax_by_returned_quantity(self):
		"""Returning half the qty of an item credits half its tax."""
		credit_note = self._credit_note(
			items=[
				{"item_code": "ITEM-A", "qty": 1.0, "sales_invoice_item": "SI-A1"},
				{"item_code": "ITEM-A", "qty": 1.0, "sales_invoice_item": "SI-A2"},
			],
			item_wise_tax_detail={"ITEM-A": [18.0, 36.0]},
		)

		_handle_partial_returns(credit_note, ["SI-A1"])

		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 18.0)

	def test_survives_tax_detail_naming_an_absent_item(self):
		"""The breakup can name an item that isn't on the credit note."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail={"ITEM-A": [18.0, 18.0], "ITEM-GHOST": [18.0, 18.0]},
		)

		_handle_partial_returns(credit_note, ["SI-A"])

		# only the item on the document contributes tax
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 18.0)

	def test_skips_tax_rows_without_item_wise_detail(self):
		"""Tax rows without an item wise breakup are left alone."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail=None,
			tax_amount=50.0,
		)

		_handle_partial_returns(credit_note, ["SI-A"])

		self.assertEqual(credit_note.taxes[0].tax_amount, 50.0)


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
