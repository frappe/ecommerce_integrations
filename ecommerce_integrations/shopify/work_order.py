import io

import frappe
from barcode import get_barcode_class
from barcode.writer import ImageWriter
from frappe.core.doctype.file.utils import remove_file_by_url
from frappe.utils import cstr, scrub
from frappe.utils.file_manager import save_file

from ecommerce_integrations.shopify.constants import ORDER_NUMBER_FIELD


def set_shopify_order_barcode(doc, method=None):
	"""Populate Work Order custom barcode field using linked Sales Order."""

	if not hasattr(doc, "custom_order_barcode"):
		return

	sales_order = doc.get("sales_order")
	if not sales_order:
		_clear_existing_barcode(doc)
		return

	if doc.custom_order_barcode:
		return

	shopify_order_number = frappe.db.get_value("Sales Order", sales_order, ORDER_NUMBER_FIELD)
	if not shopify_order_number:
		_clear_existing_barcode(doc)
		return

	doc.custom_order_barcode = _generate_barcode_attachment(doc, shopify_order_number)


def _generate_barcode_attachment(doc, order_number: str) -> str:
	barcode_class = get_barcode_class("code128")
	buffer = io.BytesIO()

	barcode_obj = barcode_class(cstr(order_number), writer=ImageWriter())
	barcode_obj.write(
		buffer,
		{
			"write_text": False,
			"module_width": 0.2,
			"module_height": 15.0,
			"quiet_zone": 3.0,
		},
	)

	buffer.seek(0)

	file_name = f"{scrub(doc.name or 'work-order')}-{scrub(order_number)}-barcode.png"
	file_doc = save_file(file_name, buffer.getvalue(), doc.doctype, doc.name, is_private=0)

	return file_doc.file_url


def _clear_existing_barcode(doc):
	if doc.custom_order_barcode:
		remove_file_by_url(doc.custom_order_barcode, doc.doctype, doc.name)
	doc.custom_order_barcode = ""

