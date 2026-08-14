"""Tests for Unicommerce return and credit note functionality."""

import json
from unittest.mock import MagicMock, patch

import frappe

from ecommerce_integrations.unicommerce.cancellation_and_returns import (
	_get_invoice_for_return,
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
		from types import SimpleNamespace

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

	def test_division_by_zero_protection(self):
		"""Prevent crash when tax breakup references items absent from credit note."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail={"ITEM-B": [18.0, 18.0]},  # ITEM-B not in credit note
		)

		# Should not raise division by zero
		_handle_partial_returns(credit_note, ["SI-A"])

		# Tax should be zero since no matching items
		self.assertAlmostEqual(credit_note.taxes[0].tax_amount, 0.0)

	def test_skips_tax_rows_without_item_wise_detail(self):
		"""Tax rows without an item wise breakup are left alone."""
		credit_note = self._credit_note(
			items=[{"item_code": "ITEM-A", "qty": 1.0, "sales_invoice_item": "SI-A"}],
			item_wise_tax_detail=None,
			tax_amount=50.0,
		)

		_handle_partial_returns(credit_note, ["SI-A"])

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
