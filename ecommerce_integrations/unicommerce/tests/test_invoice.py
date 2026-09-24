import base64
import unittest

import responses

import frappe
from frappe.test_runner import make_test_records

from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	ORDER_CODE_FIELD,
	ORDER_DISPLAY_CODE_FIELD,
	SETTINGS_DOCTYPE,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.invoice import (
	_get_charge_items,
	_get_charge_line_items,
	bulk_generate_invoices,
	create_sales_invoice,
)
from ecommerce_integrations.unicommerce.order import create_order, get_taxes
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient
from ecommerce_integrations.unicommerce.tests.utils import line_item_with_charges


class TestUnicommerceInvoice(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_records("Unicommerce Channel")

	def test_get_tax_lines(self):
		invoice = self.load_fixture("invoice-SDU0010")["invoice"]
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")

		taxes = get_taxes(invoice["invoiceItems"], channel_config)

		created_tax = sum(d["tax_amount"] for d in taxes)
		expected_tax = sum(item["totalTax"] for item in invoice["invoiceItems"])

		self.assertAlmostEqual(created_tax, expected_tax)

	def test_get_charge_line_items(self):
		"""Charges are billed as one item per tax rate."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		charge_items = {
			("cash_on_delivery_charges", 18.0): "COD-CHARGES-18",
			("cash_on_delivery_charges", 5.0): "COD-CHARGES-5",
		}

		line_items = [
			line_item_with_charges(cod_charge=40.0),
			line_item_with_charges(cod_charge=35.0),
			line_item_with_charges(cod_charge=25.0, tax_rate=5.0),
		]

		charge_lines = _get_charge_line_items(line_items, "Main - _TC", charge_items, channel_config)
		charge_by_item = {line["item_code"]: line for line in charge_lines}

		self.assertEqual(len(charge_lines), 2)
		self.assertEqual(charge_by_item["COD-CHARGES-18"]["rate"], 75.0)
		self.assertEqual(charge_by_item["COD-CHARGES-5"]["rate"], 25.0)

		for charge_line in charge_lines:
			self.assertEqual(charge_line["qty"], 1)
			self.assertEqual(charge_line["income_account"], channel_config.cod_account)
			self.assertNotIn("warehouse", charge_line)

	def test_get_charge_line_items_of_unconfigured_rate(self):
		"""Invoice is refused for a charge at a tax rate with no charge item."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=100.0, tax_rate=5.0)

		self.assertRaises(
			frappe.ValidationError,
			_get_charge_line_items,
			[line_item],
			"Main - _TC",
			{("cash_on_delivery_charges", 18.0): "COD-CHARGES"},
			channel_config,
		)

	def test_get_charge_line_items_of_unconfigured_charge(self):
		"""Invoice is refused for a taxed charge with no charge item."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=100.0)
		line_item["giftWrapCharges"] = 20.0

		self.assertRaises(
			frappe.ValidationError,
			_get_charge_line_items,
			[line_item],
			"Main - _TC",
			{("cash_on_delivery_charges", 18.0): "COD-CHARGES"},
			channel_config,
		)

	def test_get_charge_line_items_keeps_untaxed_charges_as_tax_rows(self):
		"""Untaxed charge with no charge item stays a tax row."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=100.0, tax_rate=0.0)
		line_item["giftWrapCharges"] = 20.0

		charge_lines = _get_charge_line_items(
			[line_item], "Main - _TC", {("cash_on_delivery_charges", 0.0): "COD-CHARGES"}, channel_config
		)

		self.assertEqual(
			[(line["item_code"], line["rate"]) for line in charge_lines], [("COD-CHARGES", 100.0)]
		)

	def test_get_charge_line_items_when_charges_are_not_billed_as_items(self):
		"""No charge lines when charges are not billed as items."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=100.0)
		line_item["giftWrapCharges"] = 20.0

		self.assertEqual(_get_charge_line_items([line_item], "Main - _TC", {}, channel_config), [])

	def test_get_charge_line_items_without_charges(self):
		"""No charge lines when there are no charges."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = self.load_fixture("invoice-SDU0010")["invoice"]["invoiceItems"][0]

		charge_lines = _get_charge_line_items(
			[line_item], "Main - _TC", {("cash_on_delivery_charges", 18.0): "COD-CHARGES"}, channel_config
		)

		self.assertEqual(charge_lines, [])

	def test_get_charge_items(self):
		"""Charge items are read from settings by tax head and rate."""
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		settings.add_charges_as_items = 1
		settings.charge_items = []

		for charge, item_code in (
			("Cash On Delivery Charges", "COD-CHARGES"),
			("Gift Wrap Charges", "GIFT-WRAP-CHARGES"),
			("Shipping Charges", "SHIPPING-CHARGES"),
		):
			settings.append("charge_items", {"charge": charge, "tax_rate": 18.0, "item_code": item_code})

		self.assertEqual(
			_get_charge_items(settings),
			{
				("cash_on_delivery_charges", 18.0): "COD-CHARGES",
				("gift_wrap_charges", 18.0): "GIFT-WRAP-CHARGES",
				("shipping_charges", 18.0): "SHIPPING-CHARGES",
				("shipping_method_charges", 18.0): "SHIPPING-CHARGES",
			},
		)

		settings.add_charges_as_items = 0
		self.assertEqual(_get_charge_items(settings), {})

	@unittest.skip("Too similar to e2e test down below")
	def test_create_invoice(self):
		"""Use mocked invoice json to create and assert synced fields"""
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so = create_order(order, client=self.client)

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]
		label = self.load_fixture("invoice_label_response")["label"]

		si = create_sales_invoice(si_data=si_data, so_code=so.name, shipping_label=label)

		self.assertEqual(si.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(si.get(ORDER_DISPLAY_CODE_FIELD), order["displayOrderCode"])
		self.assertEqual(si.get(FACILITY_CODE_FIELD), "Test-123")
		self.assertEqual(si.get(INVOICE_CODE_FIELD), si_data["code"])
		self.assertEqual(si.get(SHIPPING_PACKAGE_CODE_FIELD), si_data["shippingPackageCode"])

		self.assertAlmostEqual(si.grand_total, 7028)
		self.assertEqual(si.update_stock, 0)

		# check that pdf invoice got synced
		attachments = frappe.get_all(
			"File", fields=["name", "file_name"], filters={"attached_to_name": si.name}
		)
		self.assertGreaterEqual(len(attachments), 2, msg=f"Expected 2 attachments, found: {attachments!s}")

	def test_end_to_end_invoice_generation(self):
		"""Full invoice generation test with mocked responses."""

		from ecommerce_integrations.unicommerce import invoice

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]

		# HACK to allow invoicing test
		invoice.INVOICED_STATE.append("CREATED")
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/oms/shippingPackage/createInvoiceAndAllocateShippingProvider",
			status=200,
			json=self.load_fixture("create_invoice_and_assign_shipper"),
			match=[responses.json_params_matcher({"shippingPackageCode": "TEST00949"})],
		)
		self.responses.add(
			responses.POST,
			"https://demostaging.unicommerce.com/services/rest/v1/invoice/details/get",
			status=200,
			json=self.load_fixture("invoice-SDU0026"),
			match=[responses.json_params_matcher({"shippingPackageCode": "TEST00949", "return": False})],
		)
		self.responses.add(
			responses.GET,
			"https://example.com",
			status=200,
			body=base64.b64decode(self.load_fixture("invoice_label_response")["label"]),
		)

		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so = create_order(order, client=self.client)
		make_stock_entry(item_code="MC-100", qty=15, to_warehouse="Stores - WP", rate=42)

		bulk_generate_invoices(sales_orders=[so.name], client=self.client)

		sales_invoice_code = frappe.db.get_value("Sales Invoice", {INVOICE_CODE_FIELD: "SDU0026"})

		if not sales_invoice_code:
			self.fail("Sales invoice not generated")

		si = frappe.get_doc("Sales Invoice", sales_invoice_code)

		self.assertEqual(si.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(si.get(ORDER_DISPLAY_CODE_FIELD), order["displayOrderCode"])
		self.assertEqual(si.get(FACILITY_CODE_FIELD), "Test-123")
		self.assertEqual(si.get(INVOICE_CODE_FIELD), si_data["code"])
		self.assertEqual(si.get(SHIPPING_PACKAGE_CODE_FIELD), si_data["shippingPackageCode"])

		self.assertAlmostEqual(si.grand_total, 7028)

		# check that pdf invoice got synced
		attachments = frappe.get_all(
			"File", fields=["name", "file_name"], filters={"attached_to_name": si.name}
		)
		self.assertGreaterEqual(len(attachments), 2, msg=f"Expected 2 attachments, found: {attachments!s}")
