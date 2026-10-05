from dataclasses import dataclass

import frappe
from erpnext.stock.doctype.batch.batch import Batch
from frappe import _
from frappe.utils import cint, getdate
from frappe.utils.csvutils import UnicodeWriter
from frappe.utils.file_manager import save_file

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	GRN_STOCK_ENTRY_TYPE,
	MODULE_NAME,
	SETTINGS_DOCTYPE,
)
from ecommerce_integrations.unicommerce.utils import remove_non_alphanumeric_chars

CSV_HEADER_LINE = (
	"Vendor Code*,Vendor Invoice Number*,Purchase Order Code,Vendor Invoice Date*,Sku"
	" Code*,Qty*,Item Code,Item Details,Shelf Code,MRP,Unit Price,Manufacturing Date,Expiry date"
	" as dd/MM/yyyy,Vendor Batch Number\r\n"
)


@dataclass
class GRNItemRow:
	vendor_code: str
	vendor_invoice_number: str
	invoice_date: str
	sku: str
	qty: int
	item_code: str
	purchase_order: str = ""
	manufacturing_date: str = ""
	expiry_date: str = ""
	batch_number: str = ""
	shelf_code: str = ""
	item_details: str = ""
	mrp: str = 0.0
	unit_price: str = 0.0

	def get_ordered_fields(self):
		return [
			self.vendor_code,
			self.vendor_invoice_number,
			self.purchase_order,
			self.invoice_date,
			self.sku,
			self.qty,
			self.item_code,
			self.item_details,
			self.shelf_code,
			self.mrp,
			self.unit_price,
			self.manufacturing_date,
			self.expiry_date,
			self.batch_number,
		]


def is_unicommerce_grn(stock_entry) -> bool:
	if stock_entry.stock_entry_type != GRN_STOCK_ENTRY_TYPE:
		return False

	grn_enabled = frappe.db.get_single_value(SETTINGS_DOCTYPE, "use_stock_entry_for_grn")
	if not grn_enabled:
		frappe.throw(
			_("Auto GRN not enabled in Unicommerce settings. Can not use Stock Entry Type: {}").format(
				GRN_STOCK_ENTRY_TYPE
			)
		)
	return True


def validate_stock_entry_for_grn(doc, method=None):
	stock_entry = doc
	if not is_unicommerce_grn(stock_entry):
		return

	settings = frappe.get_doc(SETTINGS_DOCTYPE)

	if not settings.is_enabled():
		return

	get_facility_code(stock_entry, settings)


def get_facility_code(stock_entry, unicommerce_settings) -> str:
	"""Validate that facility has single warehouse and return facility code."""

	target_warehouses = {d.t_warehouse for d in stock_entry.items}
	if len(target_warehouses) > 1:
		frappe.throw(
			_("{} only supports one target warehouse (unicommerce facility)").format(GRN_STOCK_ENTRY_TYPE)
		)

	warehouse = next(iter(target_warehouses))
	warehouse_mapping = unicommerce_settings.get_erpnext_to_integration_wh_mapping(all_wh=True)

	facility = warehouse_mapping.get(warehouse)
	if not facility:
		msg = _("{} warehouse does not have Unicommerce facilities mapped to it.").format(warehouse)
		frappe.throw(msg, title="Unmapped Unicommerce Facility")

	return facility


def upload_grn(doc, method=None):
	stock_entry = doc
	if not is_unicommerce_grn(stock_entry):
		return

	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	facility_code = get_facility_code(stock_entry, settings)
	csv_file = _prepare_grn_import_csv(doc)

	response = create_auto_grn_import(csv_file, facility_code=facility_code)

	if not response or not response.successful:
		frappe.throw(
			_("GRN upload failed, Unicommerce reported errors.<br>{}").format(
				"<br>".join(response.errors if response else [])
			)
		)

	errors = response.errors
	if response.successful and not errors:
		msg = _("Successully queued GRN import to Unicommerce.")
		msg += _("Confirm the status on Import Log in Uniware.")
		frappe.msgprint(msg, title="Success")
	elif response.successful and errors:
		frappe.msgprint("Partial success, unicommerce reported errors:<br>{}".format("<br>".join(errors)))


def _prepare_grn_import_csv(stock_entry) -> str:
	"""Prepare CSV file in Unicommerce auto grn api format and attach it to Stock Entry
	returns: filename of generated csv.
	"""

	rows = []
	vendor_code = frappe.db.get_single_value(SETTINGS_DOCTYPE, "vendor_code")

	for item in stock_entry.items:
		price = frappe.db.get_value("Item", item.item_code, "standard_rate") or ""
		invoice_date = _get_unicommerce_format_date(stock_entry.posting_date)

		batch_details = frappe.db.get_value(
			"Batch", item.batch_no, fieldname=["manufacturing_date", "expiry_date"], as_dict=True
		)
		manufacturing_date = _get_unicommerce_format_date(
			batch_details.manufacturing_date if batch_details else getdate()
		)
		expiry_date = _get_unicommerce_format_date(
			batch_details.expiry_date if batch_details else getdate("2099-01-01")
		)

		sku = frappe.db.get_value(
			"Ecommerce Item",
			{"erpnext_item_code": item.item_code, "integration": MODULE_NAME},
			"integration_item_code",
		)
		if not sku:
			frappe.throw(_("Item {} does not have associated Unicommerce SKU.").format(item.item_code))

		row = GRNItemRow(
			vendor_code=vendor_code,
			vendor_invoice_number=stock_entry.name,
			invoice_date=invoice_date,
			sku=sku,
			qty=cint(item.qty),  # implicitly round down
			item_code=sku,
			manufacturing_date=manufacturing_date,
			expiry_date=expiry_date,
			batch_number=item.batch_no,
			mrp=price,
			unit_price=price,
		)
		rows.append(row)

	file_name = remove_non_alphanumeric_chars(stock_entry.name)
	file = save_file(
		fname=f"GRN-{file_name}.csv",
		content=_get_csv_content(rows),
		dt=stock_entry.doctype,
		dn=stock_entry.name,
	)
	return file.file_name


def _get_csv_content(rows: list[GRNItemRow]) -> bytes:
	writer = UnicodeWriter()

	for row in rows:
		writer.writerow(row.get_ordered_fields())

	csv_content = CSV_HEADER_LINE + writer.getvalue()
	return csv_content.encode("utf-8")


def _get_unicommerce_format_date(date) -> str:
	if date:
		return getdate(date).strftime("%d/%m/%Y")
	return ""


def create_auto_grn_import(csv_filename: str, facility_code: str, client=None):
	"""Create new import job for Auto GRN items"""
	if client is None:
		client = UnicommerceAPIClient()
	resp = client.create_import_job(
		job_name="Auto GRN Items", csv_filename=csv_filename, facility_code=facility_code
	)
	return resp


def prevent_grn_cancel(doc, method=None):
	if not is_unicommerce_grn(doc):
		return

	msg = _("This Stock Entry can not be cancelled.")
	msg += _("To undo this stock entry you need to move the Stock back") + " "
	msg += _("and remove stock from Unicommerce.")

	frappe.throw(msg, title="GRN Stock Entry can not be cancelled")
