import base64
import unittest
from io import BytesIO
from unittest.mock import patch

import frappe
import responses
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.utils.pdf import pdf_contains_js
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	ORDER_DISPLAY_CODE_FIELD,
	ORDER_INVOICE_STATUS_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.invoice import bulk_generate_invoices, create_sales_invoice
from ecommerce_integrations.unicommerce.order import create_order, get_taxes
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient
from ecommerce_integrations.unicommerce.utils import strip_pdf_javascript


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
		self.assertGreaterEqual(len(attachments), 1, msg=f"Expected 1 attachments, found: {attachments!s}")

	def test_unsafe_pdf_is_sanitized_and_attached(self):
		"""Invoice must sync and keep its PDF even when the marketplace PDF embeds JavaScript."""
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so = create_order(order, client=self.client)

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]
		# unique code: tests in a class share one transaction, the e2e test above
		# already synced invoice code SDU0026 and create_sales_invoice skips those
		si_data["code"] = "SDU9001"
		# unicommerce invoices embed auto-print JavaScript, which frappe refuses;
		# build an equivalent PDF so the rejection path is exercised for real
		writer = PdfWriter()
		writer.add_blank_page(width=100, height=100)
		writer.add_js("this.print();")
		pdf = BytesIO()
		writer.write(pdf)
		si_data["encodedInvoice"] = base64.b64encode(pdf.getvalue()).decode()

		si = create_sales_invoice(si_data=si_data, so_code=so.name, so_data=order)

		self.assertEqual(si.get(INVOICE_CODE_FIELD), si_data["code"])
		self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "docstatus"), 1)

		# the stored PDF is the same document, minus the script
		attachments = frappe.get_all(
			"File",
			fields=["name"],
			filters={
				"attached_to_name": si.name,
				"attached_to_doctype": "Sales Invoice",
				"file_name": ("like", "unicommerce-invoice-SDU9001%"),
			},
		)
		self.assertEqual(len(attachments), 1, msg=f"Expected 1 attachment, found: {attachments!s}")
		content = frappe.get_doc("File", attachments[0].name).get_content()
		self.assertTrue(content.startswith(b"%PDF"))
		self.assertFalse(pdf_contains_js(content))

		# the rejection must not surface as an error dialog to the user
		self.assertNotIn("unsafe content", str(frappe.local.message_log))

		# nothing to complain about: the attachment was saved
		comments = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": si.name,
				"comment_type": "Comment",
			},
		)
		self.assertEqual(len(comments), 0, msg=f"Unexpected comments: {comments!s}")

	def test_attachment_failure_fails_the_sync(self):
		"""A PDF that cannot be saved must fail the sync so a retry starts clean."""
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so = create_order(order, client=self.client)

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]
		si_data["code"] = "SDU9002"  # unique, see test above

		with (
			patch("ecommerce_integrations.unicommerce.invoice.save_file") as save_file_mock,
			self.assertRaisesRegex(Exception, "disk full"),
		):
			save_file_mock.side_effect = Exception("disk full")
			create_sales_invoice(si_data=si_data, so_code=so.name, so_data=order)

	def test_annotation_javascript_is_stripped(self):
		"""frappe scans whole pages, so JS in annotation actions must be removed too."""
		writer = PdfWriter()
		writer.add_blank_page(width=100, height=100)
		writer.pages[0].get_object()[NameObject("/Annots")] = ArrayObject(
			[
				DictionaryObject(
					{
						NameObject("/Type"): NameObject("/Annot"),
						NameObject("/Subtype"): NameObject("/Link"),
						NameObject("/Rect"): ArrayObject([FloatObject(10)] * 4),
						NameObject("/A"): DictionaryObject(
							{
								NameObject("/S"): NameObject("/JavaScript"),
								NameObject("/JS"): TextStringObject("app.alert('hi');"),
							}
						),
					}
				)
			]
		)
		pdf = BytesIO()
		writer.write(pdf)

		self.assertTrue(pdf_contains_js(pdf.getvalue()))

		sanitized = strip_pdf_javascript(base64.b64encode(pdf.getvalue()))
		self.assertIsNotNone(sanitized)
		self.assertFalse(pdf_contains_js(sanitized))

	def test_nested_javascript_action_is_stripped(self):
		"""JavaScript chained after a safe action must also be removed."""
		writer = PdfWriter()
		writer.add_blank_page(width=100, height=100)
		writer.pages[0].get_object()[NameObject("/Annots")] = ArrayObject(
			[
				DictionaryObject(
					{
						NameObject("/Type"): NameObject("/Annot"),
						NameObject("/Subtype"): NameObject("/Link"),
						NameObject("/Rect"): ArrayObject([FloatObject(10)] * 4),
						NameObject("/A"): DictionaryObject(
							{
								NameObject("/S"): NameObject("/URI"),
								NameObject("/URI"): TextStringObject("https://example.com"),
								NameObject("/Next"): DictionaryObject(
									{
										NameObject("/S"): NameObject("/JavaScript"),
										NameObject("/JS"): TextStringObject("app.alert('hi');"),
									}
								),
							}
						),
					}
				)
			]
		)
		pdf = BytesIO()
		writer.write(pdf)

		sanitized = strip_pdf_javascript(base64.b64encode(pdf.getvalue()))

		self.assertIsNotNone(sanitized)
		self.assertFalse(pdf_contains_js(sanitized))
		# the link keeps its URI action, only the chained script is gone
		reader = PdfReader(BytesIO(sanitized))
		action = reader.pages[0]["/Annots"][0].get_object()["/A"]
		self.assertEqual(action["/S"], "/URI")
		self.assertNotIn("/Next", action)

	def test_page_destination_open_action_is_preserved(self):
		"""A page-destination /OpenAction is not JavaScript and must survive."""
		writer = PdfWriter()
		writer.add_blank_page(width=100, height=100)
		writer.root_object[NameObject("/OpenAction")] = ArrayObject(
			[writer.pages[0].indirect_reference, NameObject("/Fit")]
		)
		pdf = BytesIO()
		writer.write(pdf)

		# frappe accepts destinations, so the sanitizer must not drop them
		self.assertFalse(pdf_contains_js(pdf.getvalue()))

		sanitized = strip_pdf_javascript(base64.b64encode(pdf.getvalue()))
		self.assertIsNotNone(sanitized)

		reader = PdfReader(BytesIO(sanitized))
		self.assertIn("/OpenAction", reader.trailer["/Root"])

	def test_batch_failure_keeps_earlier_invoices(self):
		"""A failing order must not roll back invoices created earlier in the batch."""
		order_a = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so_a = create_order(order_a, client=self.client)

		# a second, distinct order so the failed order's cleanup is observable
		order_b = self.load_fixture("order-SO5906")["saleOrderDTO"]
		order_b["code"] = "SO5906B"
		so_b = create_order(order_b, client=self.client)

		si_data_a = self.load_fixture("invoice-SDU0026")["invoice"]
		si_data_a["code"] = "SDU9005"  # unique, see tests above

		si_data_b = self.load_fixture("invoice-SDU0026")["invoice"]
		si_data_b["code"] = "SDU9006"

		created = {}

		def create_invoice(*args, **kwargs):
			si = create_sales_invoice(si_data=si_data_a, so_code=so_a.name, so_data=order_a)
			created["a"] = si.name

		def create_then_fail(*args, **kwargs):
			# fails mid-order: after the invoice and its attachments exist
			si = create_sales_invoice(si_data=si_data_b, so_code=so_b.name, so_data=order_b)
			created["b"] = si.name
			raise Exception("disk full")

		# list side_effect items are returned, not invoked; dispatch via a callable
		actions = iter([create_invoice, create_then_fail])

		def dispatch(*args, **kwargs):
			return next(actions)(*args, **kwargs)

		with patch("ecommerce_integrations.unicommerce.invoice._generate_invoice") as generate_mock:
			generate_mock.side_effect = dispatch
			bulk_generate_invoices(sales_orders=[so_a.name, so_b.name], client=self.client)

		# the first order's invoice survived the second order's failure
		self.assertEqual(frappe.db.get_value("Sales Invoice", created["a"], "docstatus"), 1)

		# the failed order's invoice and attachments were rolled back
		self.assertFalse(frappe.db.exists("Sales Invoice", {INVOICE_CODE_FIELD: "SDU9006"}))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": created["b"]}))

		# each order reports its own outcome
		self.assertEqual(frappe.db.get_value("Sales Order", so_a.name, ORDER_INVOICE_STATUS_FIELD), "Success")
		self.assertEqual(frappe.db.get_value("Sales Order", so_b.name, ORDER_INVOICE_STATUS_FIELD), "Failed")

		# the batch reports partial success
		log = frappe.get_last_doc(
			"Ecommerce Integration Log", filters={"integration": MODULE_NAME, "status": "Partial Success"}
		)
		self.assertIn(so_b.name, log.message)

	def test_invoice_sync_survives_missing_package_code(self):
		"""An absent shipping package code must not fail invoice sync."""
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]
		so = create_order(order, client=self.client)

		si_data = self.load_fixture("invoice-SDU0026")["invoice"]
		si_data["code"] = "SDU9003"  # unique, see test above
		si_data.pop("shippingPackageCode", None)

		si = create_sales_invoice(si_data=si_data, so_code=so.name, so_data=order)

		self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "docstatus"), 1)

		# save_file reuses the first file name for identical content (hash dedup),
		# so the shared fixture PDF may keep an earlier test's code in its name
		attachments = frappe.get_all(
			"File",
			filters={"attached_to_name": si.name, "file_name": ("like", "unicommerce-invoice-%")},
		)
		self.assertEqual(len(attachments), 1, msg=f"Expected 1 attachment, found: {attachments!s}")
