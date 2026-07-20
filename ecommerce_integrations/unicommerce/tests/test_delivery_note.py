import base64
import unittest

import frappe
import responses
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	ORDER_CODE_FIELD,
	ORDER_DISPLAY_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.delivery_note import create_delivery_note
from ecommerce_integrations.unicommerce.invoice import bulk_generate_invoices, create_sales_invoice
from ecommerce_integrations.unicommerce.order import create_order
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestDeliveryNote(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

	def test_create_invoice_and_delivery_note(self):
		"""Use mocked invoice json to create and assert synced fields"""
		from ecommerce_integrations.unicommerce import invoice

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
		dn = create_delivery_note(so, si)
		self.assertEqual(dn.unicommerce_order_code, so.unicommerce_order_code)
		self.assertEqual(dn.get(ORDER_DISPLAY_CODE_FIELD), so.get(ORDER_DISPLAY_CODE_FIELD))

	def test_backfill_display_order_code_on_resync(self):
		"""Re-syncing an order backfills the display order no. on the order and on the
		invoices / delivery notes already made from it before the field existed."""
		from erpnext.selling.doctype.sales_order.mapper import make_delivery_note, make_sales_invoice

		# order-SO5905 is not invoiced by other tests, so this stays isolated.
		order = self.load_fixture("order-SO5905")["saleOrderDTO"]
		display_code = order["displayOrderCode"]

		so = create_order(order, client=self.client)

		# An invoice and delivery note that predate the field (no display order no. yet).
		si = make_sales_invoice(so.name)
		si.insert(ignore_permissions=True)

		dn = make_delivery_note(so.name)
		dn.unicommerce_order_code = so.unicommerce_order_code
		dn.insert(ignore_permissions=True)

		linked = (
			("Sales Order", so.name),
			("Sales Invoice", si.name),
			("Delivery Note", dn.name),
		)

		# Simulate the pre-patch state: no display order no. anywhere.
		for doctype, name in linked:
			frappe.db.set_value(doctype, name, ORDER_DISPLAY_CODE_FIELD, "")

		# Re-sync the same order; the payload now carries displayOrderCode.
		create_order(order, client=self.client)

		for doctype, name in linked:
			self.assertEqual(
				frappe.db.get_value(doctype, name, ORDER_DISPLAY_CODE_FIELD),
				display_code,
				msg=f"{doctype} {name}: display order no. not backfilled",
			)
