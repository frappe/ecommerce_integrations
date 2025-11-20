import os
import re

import frappe
import shopify
from barcode import Code128
from barcode.writer import ImageWriter
from frappe.core.doctype.file.utils import remove_file_by_url
from frappe.utils import cstr
from frappe.utils.file_manager import save_file

from ecommerce_integrations.shopify.constants import ORDER_NUMBER_FIELD
from ecommerce_integrations.shopify.connection import temp_shopify_session


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


def tag_shopify_order_in_production(doc, method=None):
	"""Add IN PRODUCTION tag to Shopify order when Work Order moves to In Process."""

	try:
		if isinstance(doc, str):
			doc = frappe.get_doc("Work Order", doc)

		doc.reload()

		if doc.status != "In Process":
			return

		if not doc.sales_order:
			return

		shopify_order_id = frappe.db.get_value("Sales Order", doc.sales_order, "shopify_order_id")
		if not shopify_order_id:
			return

		_update_shopify_order_tag(shopify_order_id, "IN PRODUCTION")
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"Shopify Work Order Tag Update Failed",
		)


def handle_stock_entry_shopify_tag(doc, method=None):
	"""Trigger Shopify tagging when stock entry pushes Work Order to In Process."""

	if doc.purpose not in {"Manufacture", "Material Transfer for Manufacture"}:
		return

	if not doc.work_order:
		return

	tag_shopify_order_in_production(doc.work_order)


@temp_shopify_session
def _update_shopify_order_tag(shopify_order_id, tag):
	order = shopify.Order.find(shopify_order_id)
	if not order:
		return

	existing_tags = [t.strip() for t in (order.tags or "").split(",") if t.strip()]
	if tag not in existing_tags:
		existing_tags.append(tag)
		order.tags = ", ".join(existing_tags)
		try:
			order.save()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				"Shopify Order Tag Save Failed",
			)


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

