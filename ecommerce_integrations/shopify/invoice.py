import frappe

from frappe.utils import cstr, cint, nowdate
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.shopify.constants import (
	SETTING_DOCTYPE,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
)


def prepare_sales_invoice(order, request_id=None):
	from ecommerce_integrations.shopify.order import get_sales_order

	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_sales_invoice(order, setting, sales_order)
			create_shopify_log(status="Success")
		else:
			create_shopify_log(status="Invalid", message="Sales Order not found for syncing sales invoice.")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_sales_invoice(shopify_order, setting, so):
	if (
		not frappe.db.get_value(
			"Sales Invoice", {ORDER_ID_FIELD: shopify_order.get("id")}, "name"
		)
		and so.docstatus == 1
		and not so.per_billed
		and cint(setting.sync_sales_invoice)
	):

		posting_date = nowdate()

		sales_invoice = make_sales_invoice(so.name, ignore_permissions=True)
		setattr(sales_invoice, ORDER_ID_FIELD, shopify_order.get("id"))
		setattr(sales_invoice, ORDER_NUMBER_FIELD, shopify_order.get("name"))
		sales_invoice.set_posting_time = 1
		sales_invoice.posting_date = posting_date
		sales_invoice.due_date = posting_date
		sales_invoice.naming_series = setting.sales_invoice_series or "SI-Shopify-"
		sales_invoice.flags.ignore_mandatory = True
		set_cost_center(sales_invoice.items, setting.cost_center)
		sales_invoice.insert(ignore_mandatory=True)
		sales_invoice.submit()
		make_payament_entry_against_sales_invoice(sales_invoice, setting, posting_date)
		frappe.db.commit()


def set_cost_center(items, cost_center):
	for item in items:
		item.cost_center = cost_center


def make_payament_entry_against_sales_invoice(doc, setting, posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(
		doc.doctype, doc.name, bank_account=setting.cash_bank_account
	)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = posting_date or nowdate()
	payment_entry.reference_date = posting_date or nowdate()
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()
