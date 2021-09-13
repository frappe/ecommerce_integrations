import frappe

from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	ORDER_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.invoice import create_sales_invoice
from ecommerce_integrations.unicommerce.order import create_order, get_taxes
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceInvoice(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

	def test_get_tax_lines(self):
		invoice = self.load_fixture("invoice-SDU0010")["invoice"]
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")

		taxes = get_taxes(invoice["invoiceItems"], channel_config)

		created_tax = sum(d["tax_amount"] for d in taxes)
		expected_tax = sum(item["totalTax"] for item in invoice["invoiceItems"])

		self.assertAlmostEqual(created_tax, expected_tax)

	def test_create_invoice(self):
		"""Use mocked invoice json to create and assert synced fields"""
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so = create_order(order, client=self.client)

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]

		si = create_sales_invoice(si_data=si_data, so_code=so.name)

		self.assertEqual(si.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(si.get(FACILITY_CODE_FIELD), "Test-123")
		self.assertEqual(si.get(INVOICE_CODE_FIELD), si_data["code"])
		self.assertEqual(si.get(SHIPPING_PACKAGE_CODE_FIELD), si_data["shippingPackageCode"])

		self.assertAlmostEqual(si.grand_total, 7028)
		self.assertEqual(si.update_stock, 0)

		# check that pdf invoice got synced
		attachments = frappe.get_all(
			"File", fields=["name", "file_name"], filters={"attached_to_name": si.name}
		)
		self.assertNotEqual([], attachments)
