import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe import _
from frappe.utils import cint, cstr, getdate, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_sales_invoice(payload, request_id=None):
	from ecommerce_integrations.shopify.order import create_sales_order, get_sales_order

	order = payload

	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_sales_invoice(order, setting, sales_order)
			create_shopify_log(status="Success")
		else:
			sales_order = create_sales_order(order, setting)
			if sales_order:
				create_sales_invoice(order, setting, sales_order)
				create_shopify_log(status="Success")
			else:
				create_shopify_log(
					status="Invalid", message="Sales Order could not be created for syncing sales invoice."
				)

	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_sales_invoice(shopify_order, setting, so):
	if (
		not frappe.db.get_value("Sales Invoice", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")
		and so.docstatus == 1
		and not so.per_billed
		and cint(setting.sync_sales_invoice)
	):
		posting_date = getdate(shopify_order.get("created_at")) or nowdate()

		sales_invoice = make_sales_invoice(so.name, ignore_permissions=True)
		sales_invoice.set(ORDER_ID_FIELD, str(shopify_order.get("id")))
		sales_invoice.set(ORDER_NUMBER_FIELD, shopify_order.get("name"))
		sales_invoice.set_posting_time = 1
		sales_invoice.posting_date = posting_date
		sales_invoice.due_date = posting_date
		sales_invoice.naming_series = setting.sales_invoice_series or "SI-Shopify-"
		sales_invoice.flags.ignore_mandatory = True
		set_cost_center(
			sales_invoice.items,
			setting.cost_center,
			sales_invoice.company,
			setting,
		)
		sales_invoice.insert(ignore_mandatory=True)
		sales_invoice.submit()
		if sales_invoice.grand_total > 0:
			make_payament_entry_against_sales_invoice(sales_invoice, setting, posting_date)

		if shopify_order.get("note"):
			sales_invoice.add_comment(text=f"Order Note: {shopify_order.get('note')}")


def set_cost_center(items, cost_center, company, setting=None):
	for item in items:
		# Cost Center
		if cost_center and not item.cost_center:
			item.cost_center = cost_center
			# Income Account resolution
		if not item.income_account:
			item.income_account = frappe.db.get_value(
				"Item Default",
				{
					"parent": item.item_code,
					"company": company,
				},
				"income_account",
			)

		if not item.income_account:
			item.income_account = frappe.db.get_value(
				"Item Group",
				item.item_group,
				"default_income_account",
			)

		if not item.income_account:
			frappe.throw(_(f"Income Account not found for Item {item.item_code}"))


def make_payament_entry_against_sales_invoice(doc, setting, posting_date=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(doc.doctype, doc.name, bank_account=setting.cash_bank_account)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = posting_date or nowdate()
	payment_entry.reference_date = posting_date or nowdate()
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()
