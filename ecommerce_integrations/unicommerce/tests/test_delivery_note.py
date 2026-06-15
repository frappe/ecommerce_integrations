import base64
from unittest.mock import patch

import frappe
import responses
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

from ecommerce_integrations.unicommerce import invoice
from ecommerce_integrations.unicommerce.constants import INVOICE_CODE_FIELD, ORDER_CODE_FIELD
from ecommerce_integrations.unicommerce.delivery_note import create_delivery_note
from ecommerce_integrations.unicommerce.order import create_order
from ecommerce_integrations.unicommerce.tests.test_client import UnicommerceClientTestSuite


class TestDeliveryNote(UnicommerceClientTestSuite):
	def test_create_invoice_and_delivery_note(self):
		"""Use mocked invoice json to create and assert synced fields"""
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
		delivery_note = create_delivery_note(sales_order, sales_invoice)

		self.assertEqual(delivery_note.get(ORDER_CODE_FIELD), sales_order.get(ORDER_CODE_FIELD))
