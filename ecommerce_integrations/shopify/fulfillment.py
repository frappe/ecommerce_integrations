import frappe

from ecommerce_integrations.shopify.utils import create_shopify_log
from frappe.utils import cstr, getdate, cint
from ecommerce_integrations.shopify.order import get_sales_order
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from ecommerce_integrations.shopify.constants import (
	SETTING_DOCTYPE,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	FULLFILLMENT_ID_FIELD,
)


def prepare_delivery_note(order, request_id=None):
	frappe.set_user("Administrator")
	shopify_setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_delivery_note(order, shopify_setting, sales_order)
		create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_delivery_note(shopify_order, shopify_setting, so):
	if not cint(shopify_setting.sync_delivery_note):
		return

	for fulfillment in shopify_order.get("fulfillments"):
		if (
			not frappe.db.get_value(
				"Delivery Note", {FULLFILLMENT_ID_FIELD: fulfillment.get("id")}, "name"
			)
			and so.docstatus == 1
		):

			dn = make_delivery_note(so.name)
			dn[ORDER_ID_FIELD] = fulfillment.get("order_id")
			dn[ORDER_NUMBER_FIELD] = shopify_order.get("name")
			dn[FULLFILLMENT_ID_FIELD] = fulfillment.get("id")
			dn.set_posting_time = 1
			dn.posting_date = getdate(fulfillment.get("created_at"))
			dn.naming_series = shopify_setting.delivery_note_series or "DN-Shopify-"
			dn.items = get_fulfillment_items(dn.items, fulfillment.get("line_items"))
			dn.flags.ignore_mandatory = True
			dn.save()
			dn.submit()
			frappe.db.commit()


def get_fulfillment_items(dn_items, fulfillment_items):
	# local import to avoid circular imports
	from ecommerce_integrations.shopify.product import get_item_code

	return [
		dn_item.update({"qty": item.get("quantity")})
		for item in fulfillment_items
		for dn_item in dn_items
		if get_item_code(item) == dn_item.item_code
	]
