import base64
import unittest
from io import BytesIO
from unittest.mock import patch

import frappe
import responses
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.test_runner import make_test_records
from frappe.utils.pdf import pdf_contains_js
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

from ecommerce_integrations.unicommerce.constants import (
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	IS_CHARGE_ROW_FIELD,
	MODULE_NAME,
	ORDER_CODE_FIELD,
	ORDER_DISPLAY_CODE_FIELD,
	ORDER_INVOICE_STATUS_FIELD,
	SETTINGS_DOCTYPE,
	SHIPPING_PACKAGE_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.invoice import (
	_get_charge_items,
	_get_charge_line_items,
	_select_package_allocation,
	bulk_generate_invoices,
	create_sales_invoice,
)
from ecommerce_integrations.unicommerce.order import create_order, get_taxes
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient
from ecommerce_integrations.unicommerce.tests.utils import line_item_with_charges
from ecommerce_integrations.unicommerce.utils import strip_pdf_javascript


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
			# the mark lets returns recognize the row even after settings change
			self.assertEqual(charge_line[IS_CHARGE_ROW_FIELD], 1)

	def test_shipping_charge_aliases_create_one_item_line(self):
		"""Both Unicommerce shipping fields consolidate into the configured item."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=0.0)
		line_item["shippingCharges"] = 20.0
		line_item["shippingMethodCharges"] = 30.0
		charge_items = {
			("shipping_charges", 18.0): "SHIPPING-CHARGES-18",
			("shipping_method_charges", 18.0): "SHIPPING-CHARGES-18",
		}

		charge_lines = _get_charge_line_items([line_item], "Main - _TC", charge_items, channel_config)

		self.assertEqual(len(charge_lines), 1)
		self.assertEqual(charge_lines[0]["item_code"], "SHIPPING-CHARGES-18")
		self.assertEqual(charge_lines[0]["rate"], 50.0)
		self.assertEqual(charge_lines[0]["income_account"], channel_config.fnf_account)

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


class TestPackageAllocation(unittest.TestCase):
	"""The order's warehouse allocation is cut down to each package's invoice."""

	@staticmethod
	def _allocation(rows):
		return [{"item_code": code, "sales_order_row": row, "warehouse": "Stores - WP"} for code, row in rows]

	def test_keeps_only_the_package_units(self):
		"""Each package invoices separately, so it takes only its own units of the allocation."""
		line_items = [
			{"itemSku": "SKU-A", "quantity": 1},
			{"itemSku": "SKU-B", "quantity": 2},
		]
		# the order's allocation: one SKU-A unit belongs to another package
		allocation = self._allocation([("SKU-A", "r1"), ("SKU-A", "r2"), ("SKU-B", "r3"), ("SKU-B", "r4")])

		with patch(
			"ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item.ecommerce_item.get_erpnext_item_code",
			side_effect=lambda **kwargs: kwargs["integration_item_code"],
		):
			package_allocation = _select_package_allocation(line_items, allocation)

		self.assertEqual([d["sales_order_row"] for d in package_allocation], ["r1", "r3", "r4"])

	def test_skips_rows_already_billed_by_an_earlier_package(self):
		"""Two packages with the same SKU must not ship against the same sales order row."""
		line_items = [{"itemSku": "SKU-A", "quantity": 1}]
		allocation = self._allocation([("SKU-A", "r1"), ("SKU-A", "r2")])

		with (
			patch(
				"ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item.ecommerce_item.get_erpnext_item_code",
				side_effect=lambda **kwargs: kwargs["integration_item_code"],
			),
			patch("frappe.get_all", return_value=["r1"]) as get_all,
		):
			package_allocation = _select_package_allocation(line_items, allocation, "SO-1")

		self.assertEqual([d["sales_order_row"] for d in package_allocation], ["r2"])
		self.assertEqual(get_all.call_args.kwargs["filters"]["sales_order"], "SO-1")

	def test_passes_empty_allocation_through(self):
		self.assertIsNone(_select_package_allocation([], None))
