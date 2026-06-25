# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils import cint, cstr, getdate, nowdate

from ecommerce_integrations.medusa.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.medusa.utils import create_medusa_log, parse_datetime


def prepare_sales_invoice(payload, request_id=None):
	"""Create (and pay) a Sales Invoice for a captured Medusa order.

	Mirrors ``shopify.invoice.prepare_sales_invoice``. Bound to the
	``order.payment_captured`` topic; the payload is a Medusa ``order`` dict.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	# the captured-payment payload may wrap the order, or be the order itself
	# Verified against @medusajs/types 2.4.0.
	order = payload.get("order") if isinstance(payload, dict) and payload.get("order") else payload

	setting = frappe.get_doc(SETTING_DOCTYPE)
	log = frappe.get_doc("Ecommerce Integration Log", request_id) if request_id else None

	try:
		if not cint(setting.sync_sales_invoice):
			_finish(log, "Invalid", "Sales Invoice sync disabled in Medusa Setting.")
			return

		order_id = cstr(order.get("id"))
		so_name = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: order_id, "docstatus": 1}, "name")
		if not so_name:
			_finish(log, "Invalid", "Sales Order not found for syncing sales invoice.")
			return

		so = frappe.get_doc("Sales Order", so_name)
		create_sales_invoice(order, setting, so)
		_finish(log, "Success")
	except Exception as e:
		create_medusa_log(status="Error", exception=e, rollback=True, request_data=payload)


def create_sales_invoice(order, setting, so):
	order_id = cstr(order.get("id"))

	# dedup on the SI's medusa_order_id
	if frappe.db.get_value("Sales Invoice", {ORDER_ID_FIELD: order_id}, "name"):
		return
	if so.docstatus != 1 or so.per_billed or not cint(setting.sync_sales_invoice):
		return

	posting_date = parse_datetime(order.get("created_at")) or nowdate()
	posting_date = getdate(posting_date)

	sales_invoice = make_sales_invoice(so.name, ignore_permissions=True)
	sales_invoice.set(ORDER_ID_FIELD, order_id)
	sales_invoice.set(ORDER_NUMBER_FIELD, cstr(order.get("display_id")))
	sales_invoice.set(ORDER_STATUS_FIELD, order.get("status"))
	sales_invoice.set_posting_time = 1
	sales_invoice.posting_date = posting_date
	sales_invoice.due_date = posting_date
	sales_invoice.naming_series = setting.sales_invoice_series or "SI-Medusa-"
	sales_invoice.flags.ignore_mandatory = True
	_set_cost_center(sales_invoice.items, setting.cost_center)
	sales_invoice.insert(ignore_mandatory=True)
	sales_invoice.submit()

	if sales_invoice.grand_total > 0:
		_make_payment_entry(sales_invoice, setting, posting_date)


def _set_cost_center(items, cost_center):
	if not cost_center:
		return
	for item in items:
		item.cost_center = cost_center


def _make_payment_entry(doc, setting, posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(doc.doctype, doc.name, bank_account=setting.cash_bank_account)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = posting_date or nowdate()
	payment_entry.reference_date = posting_date or nowdate()
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()


def _finish(log, status, message=None):
	if log:
		log.status = status
		if message:
			log.message = message
		log.save(ignore_permissions=True)
	elif status != "Success":
		create_medusa_log(status=status, message=message)
