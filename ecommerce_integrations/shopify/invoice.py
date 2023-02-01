import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils import cint, cstr, getdate, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_sales_invoice(payload, request_id=None):
	from ecommerce_integrations.shopify.order import get_sales_order

	order = payload

	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			payment = order.get("payment_terms", {}).get("payment_schedules", [])
			posting_date = getdate(payment[0]["completed_at"]) if payment else nowdate()
			if cint(setting.sync_sales_invoice_on_payment):
				create_sales_invoice(order, setting, sales_order, posting_date)
				make_payment_entry_against_sales_invoice(cstr(order["id"]), setting, posting_date)
			create_shopify_log(status="Success")
		else:
			create_shopify_log(status="Invalid", message="Sales Order not found for syncing sales invoice.")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_sales_invoice(shopify_order, setting, so, posting_date=nowdate()):
	if so.docstatus == 1:
		sales_invoice = make_sales_invoice(so.name, ignore_permissions=True)
		if not sales_invoice.items:
			return
		sales_invoice.set(ORDER_ID_FIELD, str(shopify_order.get("id")))
		sales_invoice.set(ORDER_NUMBER_FIELD, shopify_order.get("name"))
		sales_invoice.set_posting_time = 1
		sales_invoice.posting_date = posting_date
		sales_invoice.due_date = posting_date
		sales_invoice.naming_series = setting.sales_invoice_series or "SI-Shopify-"
		sales_invoice.flags.ignore_mandatory = True
		set_cost_center(sales_invoice.items, setting.cost_center)
		sales_invoice.insert(ignore_mandatory=True)
		sales_invoice.submit()

		if shopify_order.get("note"):
			sales_invoice.add_comment(text=f"Order Note: {shopify_order.get('note')}")


def set_cost_center(items, cost_center):
	for item in items:
		item.cost_center = cost_center


def make_payment_entry_against_sales_invoice(order_id, setting, posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	invoices = frappe.db.get_all(
		"Sales Invoice",
		filters={ORDER_ID_FIELD: order_id, "docstatus": 1},
		fields=["name", "due_date", "grand_total", "outstanding_amount"],
	)

	if not invoices:
		frappe.throw(frappe._("Invoices not synced to mark payment."))

	payment_entry = None

	for inv in invoices:
		if not payment_entry:
			payment_entry = get_payment_entry(
				"Sales Invoice", inv.name, bank_account=setting.cash_bank_account
			)
			continue

		payment_entry.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": inv.name,
				"bill_no": "",
				"due_date": inv.due_date,
				"total_amount": inv.grand_total,
				"outstanding_amount": inv.outstanding_amount,
				"allocated_amount": inv.outstanding_amount,
			},
		)
		payment_entry.paid_amount += inv.outstanding_amount

	if payment_entry:
		payment_entry.flags.ignore_mandatory = True
		payment_entry.reference_no = order_id
		payment_entry.posting_date = posting_date or nowdate()
		payment_entry.reference_date = posting_date or nowdate()
		payment_entry.insert(ignore_permissions=True)
		payment_entry.submit()
