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


def prepare_delivery_note(payload, request_id=None, account=None):
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	# FIXED: Use standardized account resolution
	from ecommerce_integrations.shopify.utils import resolve_account_context
	account = resolve_account_context(account)

	order = payload

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_delivery_note(order, account, sales_order)
			create_shopify_log(
				status="Success",
				account=account
			)
		else:
			create_shopify_log(
				status="Invalid", 
				message="Sales Order not found for syncing delivery note.",
				account=account
			)
	except Exception as e:
		create_shopify_log(
			status="Error", 
			exception=e, 
			rollback=True,
			account=account
		)


def create_delivery_note(shopify_order, account, so):
	if not _should_sync_delivery_note(account):
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
			dn.naming_series = _get_delivery_note_series(account)
			dn.items = get_fulfillment_items(
				dn.items, fulfillment.get("line_items"), fulfillment.get("location_id"), account
			)
			dn.flags.ignore_mandatory = True
			dn.save()
			dn.submit()

			if shopify_order.get("note"):
				dn.add_comment(text=f"Order Note: {shopify_order.get('note')}")


def get_fulfillment_items(dn_items, fulfillment_items, location_id=None, account=None):
	# local import to avoid circular imports
	from ecommerce_integrations.shopify.product import get_item_code

	fulfillment_items = deepcopy(fulfillment_items)

	# Get warehouse mapping from account or legacy setting
	if account and hasattr(account, 'warehouse_mappings'):
		# Use account-specific warehouse mappings
		wh_map = _get_warehouse_mapping(account)
		default_warehouse = _get_default_warehouse(account)
	else:
		# Fallback to legacy setting
		setting = frappe.get_cached_doc(SETTING_DOCTYPE)
		wh_map = setting.get_integration_to_erpnext_wh_mapping()
		default_warehouse = setting.warehouse

	warehouse = wh_map.get(str(location_id)) or default_warehouse

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


# Helper functions for account-aware delivery note creation

def _should_sync_delivery_note(account):
	"""Check if delivery note sync is enabled for this account."""
	if hasattr(account, 'sync_delivery_note'):
		return cint(account.sync_delivery_note)
	else:  # Legacy setting
		return cint(account.sync_delivery_note)

def _get_delivery_note_series(account):
	"""Get delivery note series from account or legacy setting."""
	if hasattr(account, 'delivery_note_series'):
		return account.delivery_note_series or "DN-Shopify-"
	else:  # Legacy setting
		return account.delivery_note_series or "DN-Shopify-"

def _get_warehouse_mapping(account):
	"""Get warehouse mapping from account."""
	if hasattr(account, 'warehouse_mappings'):
		return {
			mapping.shopify_location_id: mapping.erpnext_warehouse 
			for mapping in account.warehouse_mappings
		}
	return {}

def _get_default_warehouse(account):
	"""Get default warehouse from account or legacy setting."""
	if hasattr(account, 'warehouse'):
		return account.warehouse
	elif hasattr(account, 'warehouse_mappings') and account.warehouse_mappings:
		# Use first warehouse as default if no specific default warehouse field
		return account.warehouse_mappings[0].erpnext_warehouse
	return None
