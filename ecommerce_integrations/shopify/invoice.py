import frappe

from frappe.utils import cstr, cint, nowdate, getdate
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_sales_invoice(order, request_id=None):
	frappe.set_user("Administrator")
	shopify_setting = frappe.get_doc("Shopify Setting")
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_sales_invoice(order, shopify_setting, sales_order)
			create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def get_sales_order(shopify_order_id):
	sales_order = frappe.db.get_value(
		"Sales Order", filters={"shopify_order_id": shopify_order_id}
	)
	if sales_order:
		so = frappe.get_doc("Sales Order", sales_order)
		return so


def create_sales_invoice(shopify_order, shopify_setting, so):
	if (
		not frappe.db.get_value(
			"Sales Invoice", {"shopify_order_id": shopify_order.get("id")}, "name"
		)
		and so.docstatus == 1
		and not so.per_billed
		and cint(shopify_setting.sync_sales_invoice)
	):

		posting_date = nowdate()

		si = make_sales_invoice(so.name, ignore_permissions=True)
		si.shopify_order_id = shopify_order.get("id")
		si.shopify_order_number = shopify_order.get("name")
		si.set_posting_time = 1
		si.posting_date = posting_date
		si.due_date = posting_date
		si.naming_series = shopify_setting.sales_invoice_series or "SI-Shopify-"
		si.flags.ignore_mandatory = True
		set_cost_center(si.items, shopify_setting.cost_center)
		si.insert(ignore_mandatory=True)
		si.submit()
		make_payament_entry_against_sales_invoice(si, shopify_setting, posting_date)
		frappe.db.commit()


def set_cost_center(items, cost_center):
	for item in items:
		item.cost_center = cost_center


def make_payament_entry_against_sales_invoice(doc, shopify_setting, posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(
		doc.doctype, doc.name, bank_account=shopify_setting.cash_bank_account
	)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = posting_date or nowdate()
	payment_entry.reference_date = posting_date or nowdate()
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()
