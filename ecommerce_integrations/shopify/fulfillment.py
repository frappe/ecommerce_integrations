from copy import deepcopy

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from frappe.utils import cint, cstr, getdate

from ecommerce_integrations.shopify.constants import (
	FULLFILLMENT_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.order import get_sales_order
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_delivery_note(payload, request_id=None):
	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	order = payload

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_delivery_note(order, setting, sales_order)
			create_shopify_log(status="Success")
		else:
			create_shopify_log(status="Invalid", message="Sales Order not found for syncing delivery note.")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_delivery_note(shopify_order, setting, so):
	if not cint(setting.sync_delivery_note):
		return

	for fulfillment in shopify_order.get("fulfillments"):
		if (
			not frappe.db.get_value("Delivery Note", {FULLFILLMENT_ID_FIELD: fulfillment.get("id")}, "name")
			and so.docstatus == 1
		):
			dn = make_delivery_note(so.name)
			setattr(dn, ORDER_ID_FIELD, fulfillment.get("order_id"))
			setattr(dn, ORDER_NUMBER_FIELD, shopify_order.get("name"))
			setattr(dn, FULLFILLMENT_ID_FIELD, fulfillment.get("id"))
			dn.set_posting_time = 1
			dn.posting_date = getdate(fulfillment.get("created_at"))
			dn.naming_series = setting.delivery_note_series or "DN-Shopify-"
			dn.items = get_fulfillment_items(
				dn.items, fulfillment.get("line_items"), fulfillment.get("location_id")
			)
			dn.flags.ignore_mandatory = True
			dn.save()
			dn.submit()

			if shopify_order.get("note"):
				dn.add_comment(text=f"Order Note: {shopify_order.get('note')}")


def get_fulfillment_items(dn_items, fulfillment_items, location_id=None):
	# local import to avoid circular imports
	from ecommerce_integrations.shopify.product import get_item_code

	fulfillment_items = deepcopy(fulfillment_items)

	setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	wh_map = setting.get_integration_to_erpnext_wh_mapping()
	warehouse = wh_map.get(str(location_id)) or setting.warehouse

	final_items = []

	def find_matching_fullfilement_item(dn_item):
		nonlocal fulfillment_items

		for item in fulfillment_items:
			if get_item_code(item) == dn_item.item_code:
				fulfillment_items.remove(item)
				return item

	for dn_item in dn_items:
		if shopify_item := find_matching_fullfilement_item(dn_item):
			final_items.append(dn_item.update({"qty": shopify_item.get("quantity"), "warehouse": warehouse}))

	return final_items
