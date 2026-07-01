import base64
import unittest
from unittest.mock import patch

import frappe
import responses
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

from ecommerce_integrations.unicommerce import invoice
from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	ORDER_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.order import create_order, get_taxes
from ecommerce_integrations.unicommerce.tests.test_client import UnicommerceClientTestSuite


class TestUnicommerceInvoice(UnicommerceClientTestSuite):
	def assert_sales_invoice_synced_fields(self, sales_invoice, order, si_data):
		self.assertEqual(sales_invoice.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(sales_invoice.get(FACILITY_CODE_FIELD), "Test-123")
		self.assertEqual(sales_invoice.get(INVOICE_CODE_FIELD), si_data["code"])
		self.assertEqual(sales_invoice.get(SHIPPING_PACKAGE_CODE_FIELD), si_data["shippingPackageCode"])
		self.assertAlmostEqual(sales_invoice.grand_total, 7028)

	def get_attachments(self, sales_invoice):
		return frappe.get_all(
			"File", fields=["name", "file_name"], filters={"attached_to_name": sales_invoice.name}
		)

	def test_get_tax_lines(self):
		invoice_data = self.load_fixture("invoice-SDU0010")["invoice"]
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")

		taxes = get_taxes(invoice_data["invoiceItems"], channel_config)

		created_tax = sum(d["tax_amount"] for d in taxes)
		expected_tax = sum(item["totalTax"] for item in invoice_data["invoiceItems"])

		self.assertAlmostEqual(created_tax, expected_tax)

	@unittest.skip("Too similar to e2e test down below")
	def test_create_invoice(self):
		"""Use mocked invoice json to create and assert synced fields"""
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		sales_order = create_order(order, client=self.client)

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]
		label = self.load_fixture("invoice_label_response")["label"]

		sales_invoice = invoice.create_sales_invoice(
			si_data=si_data, so_code=sales_order.name, shipping_label=label
		)

		self.assert_sales_invoice_synced_fields(sales_invoice, order, si_data)
		self.assertEqual(sales_invoice.update_stock, 0)

		# check that pdf invoice got synced
		attachments = self.get_attachments(sales_invoice)
		self.assertGreaterEqual(len(attachments), 2, msg=f"Expected 2 attachments, found: {attachments!s}")

	def test_end_to_end_invoice_generation(self):
		"""Full invoice generation test with mocked responses."""
		si_data = self.load_fixture("invoice-SDU0026")["invoice"]

		# treat "CREATED" state as invoiced to allow testing the invoicing flow
		patcher = patch.object(invoice, "INVOICED_STATE", [*invoice.INVOICED_STATE, "CREATED"])
		patcher.start()
		self.addCleanup(patcher.stop)

		self.fake(
			"oms/shippingPackage/createInvoiceAndAllocateShippingProvider",
			request_body={"shippingPackageCode": "TEST00949"},
			json=self.load_fixture("create_invoice_and_assign_shipper"),
		)
		self.fake(
			"invoice/details/get",
			request_body={"shippingPackageCode": "TEST00949", "return": False},
			json=self.load_fixture("invoice-SDU0026"),
		)
		self.responses.add(
			responses.GET,
			"https://example.com",
			body=base64.b64decode(self.load_fixture("invoice_label_response")["label"]),
		)

		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		sales_order = create_order(order, client=self.client)
		make_stock_entry(item_code="MC-100", qty=15, to_warehouse="Stores - WP", rate=42)

		invoice.bulk_generate_invoices(sales_orders=[sales_order.name], client=self.client)

		sales_invoice_name = frappe.db.get_value("Sales Invoice", {INVOICE_CODE_FIELD: "SDU0026"})
		self.assertIsNotNone(sales_invoice_name, "Sales invoice not generated")

		sales_invoice = frappe.get_doc("Sales Invoice", sales_invoice_name)
		self.assert_sales_invoice_synced_fields(sales_invoice, order, si_data)

		# check that pdf invoice got synced
		attachments = self.get_attachments(sales_invoice)
		self.assertGreaterEqual(len(attachments), 1, msg=f"Expected 1 attachments, found: {attachments!s}")
