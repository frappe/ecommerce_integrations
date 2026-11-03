import base64
import datetime
from io import BytesIO

import frappe
from frappe.utils.file_manager import check_max_file_size
from frappe.utils.pdf import pdf_contains_js
from pypdf import PdfReader, PdfWriter

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.unicommerce.constants import MODULE_NAME

SYNC_METHODS = {
	"Items": "ecommerce_integrations.unicommerce.product.upload_new_items",
	"Orders": "ecommerce_integrations.unicommerce.order.sync_new_orders",
	"Inventory": "ecommerce_integrations.unicommerce.inventory.update_inventory_on_unicommerce",
}

DOCUMENT_URL_FORMAT = {
	"Sales Order": "https://{site}/order/orderitems?orderCode={code}",
	"Sales Invoice": "https://{site}/order/orderitems?orderCode={code}",
	"Item": "https://{site}/products/edit?sku={code}",
	"Unicommerce Shipment Manifest": "https://{site}/manifests/edit?code={code}",
	"Stock Entry": "https://{site}/grns",
}


def create_unicommerce_log(**kwargs):
	return create_log(module_def=MODULE_NAME, **kwargs)


@frappe.whitelist()
def get_unicommerce_document_url(code: str, doctype: str) -> str:
	if not isinstance(code, str):
		frappe.throw(frappe._("Invalid Document code"))

	site = frappe.db.get_single_value("Unicommerce Settings", "unicommerce_site", cache=True)
	url = DOCUMENT_URL_FORMAT.get(doctype, "")

	return url.format(site=site, code=code)


@frappe.whitelist()
def force_sync(document) -> None:
	frappe.only_for("System Manager")

	method = SYNC_METHODS.get(document)
	if not method:
		frappe.throw(frappe._("Unknown method"))
	frappe.enqueue(method, queue="long", is_async=True, **{"force": True})


def get_unicommerce_date(timestamp: int) -> datetime.date:
	"""Convert unicommerce ms timestamp to datetime."""
	return datetime.date.fromtimestamp(timestamp // 1000)


def remove_non_alphanumeric_chars(filename: str) -> str:
	return "".join(c for c in filename if c.isalpha() or c.isdigit()).strip()


def strip_pdf_javascript(content: str | bytes) -> bytes | None:
	"""Remove embedded JavaScript from a base64 encoded PDF.

	Unicommerce PDFs embed an auto-print script, which frappe refuses to store
	(frappe.utils.pdf.pdf_contains_js). Only JavaScript is removed; page
	destinations and non-script actions are preserved.

	Returns raw (not base64) PDF bytes, or None if content is not a sanitizable PDF.
	"""
	encoded = content.encode() if isinstance(content, str) else content
	try:
		decoded = base64.b64decode(b"".join(encoded.split()), validate=True)
	except ValueError:  # invalid base64
		return None

	# enforce the upload limit before parsing, not after at save time
	check_max_file_size(decoded)

	try:
		reader = PdfReader(BytesIO(decoded))
		writer = PdfWriter(clone_from=reader)
		_remove_javascript(writer.root_object)

		sanitized = BytesIO()
		writer.write(sanitized)
		sanitized_bytes = sanitized.getvalue()
	except Exception:
		return None

	# frappe re-checks at save time; never hand back a PDF it would still reject
	if pdf_contains_js(sanitized_bytes):
		return None
	return sanitized_bytes


def _remove_javascript(obj, visited: set[int] | None = None) -> bool:
	"""Drop JavaScript from a PDF object tree; return False for JS actions.

	Navigation actions and destination arrays are left untouched.
	"""
	obj = obj.get_object() if hasattr(obj, "get_object") else obj
	if not isinstance(obj, dict | list):
		return True

	if visited is None:
		visited = set()
	if id(obj) in visited:
		return True
	visited.add(id(obj))

	if isinstance(obj, dict):
		if obj.get("/S") == "/JavaScript":
			return False

		for key, value in list(obj.items()):
			if key in ("/JS", "/JavaScript") or not _remove_javascript(value, visited):
				del obj[key]
	else:
		for index in range(len(obj) - 1, -1, -1):
			if not _remove_javascript(obj[index], visited):
				del obj[index]

	return True
