import frappe
from barcode import Code128
from barcode.writer import ImageWriter
from frappe.core.doctype.file.utils import remove_file_by_url
from frappe.utils import cstr
from frappe.utils.file_manager import save_file
import os
import re

from ecommerce_integrations.shopify.constants import ORDER_NUMBER_FIELD


def set_shopify_order_barcode(doc, method=None):
	"""Populate Work Order custom barcode image using linked Sales Order."""

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
	safe_name = _sanitize_filename(f"{doc.name or 'work-order'}-{order_number}")
	file_path = frappe.get_site_path("private", "files", f"{safe_name}.png")

	code = Code128(cstr(order_number), writer=ImageWriter())
	code.save(file_path.replace(".png", ""))

	with open(file_path, "rb") as image_file:
		file_doc = save_file(f"{safe_name}.png", image_file.read(), doc.doctype, doc.name, is_private=1)

	if os.path.exists(file_path):
		os.remove(file_path)

	return file_doc.file_url


def _clear_existing_barcode(doc):
	if doc.custom_order_barcode:
		remove_file_by_url(doc.custom_order_barcode, doc.doctype, doc.name)
	doc.custom_order_barcode = ""


def _sanitize_filename(filename: str) -> str:
	filename = re.sub(r"[^\w\s.-]", "", filename or "")
	return filename.replace(" ", "_")

