from copy import deepcopy

import frappe
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from frappe.utils import cint

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	SHOPIFY_LINE_ITEM_ID_FIELD,
	SHOPIFY_RETURN_ID_FIELD,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def process_shopify_return(payload, request_id=None):
	"""
	Entry point for Shopify returns webhooks
	Handles: open | approved | closed
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	try:
		create_shopify_log(
			status="Debug",
			message=f"Received payload: {frappe.as_json(payload)}",
		)
		return_status = payload.get("status")
		shopify_return_id = payload.get("id")

		order_id = payload.get("order_id") or (payload.get("order") or {}).get("id")

		if not shopify_return_id or not order_id:
			create_shopify_log(
				status="Invalid",
				message=payload,
			)
			return

		if return_status == "open":
			_create_return_delivery_note(
				payload,
				shopify_return_id,
				order_id,
			)
			return
		if return_status == "closed":
			create_shopify_log(
				status="Ignored",
				message=f"Return {shopify_return_id} is closed",
			)
			return

		create_shopify_log(
			status="Ignored",
			message=f"Unknown return status: {return_status}",
		)

	except Exception as e:
		create_shopify_log(
			status="Error",
			exception=e,
			rollback=True,
		)


def _create_return_delivery_note(payload, shopify_return_id, order_id):
	"""
	Creates ERPNext Delivery Note Return
	"""

	if frappe.db.get_value(
		"Delivery Note",
		{"shopify_return_id": shopify_return_id},
		"name",
	):
		create_shopify_log(
			status="Ignored",
			message=f"Return {shopify_return_id} already processed",
		)
		return

	dn_name = frappe.db.get_value(
		"Delivery Note",
		{
			ORDER_ID_FIELD: order_id,
			"docstatus": 1,
		},
		"name",
	)

	if not dn_name:
		create_shopify_log(
			status="Invalid",
			message="Original Delivery Note not found",
		)
		return

	original_dn = frappe.get_doc("Delivery Note", dn_name)

	return_dn = make_return_doc("Delivery Note", original_dn.name)

	map_return_items(
		return_dn,
		payload.get("return_line_items") or [],
	)

	if not return_dn.items:
		create_shopify_log(
			status="Invalid",
			message="Approved return has no returnable items",
		)
		return

	return_dn.shopify_return_id = shopify_return_id
	return_dn.flags.ignore_mandatory = True

	return_dn.save()
	return_dn.submit()

	create_shopify_log(
		status="Success",
		message=f"Return Delivery Note created: {return_dn.name}",
	)


def map_return_items(return_dn, return_line_items):
	"""
	Map Shopify return_line_items → ERPNext Delivery Note items
	"""

	# Make a deepcopy to avoid mutating the payload
	return_line_items = deepcopy(return_line_items)

	for r_item in return_line_items:
		fulfillment_line_item = r_item.get("fulfillment_line_item") or {}
		line_item = fulfillment_line_item.get("line_item") or {}

		shopify_line_item_id = str(line_item.get("id"))
		return_qty = cint(r_item.get("quantity"))

		if not shopify_line_item_id or return_qty <= 0:
			continue

		for dn_item in return_dn.items:
			# Skip items with no Sales Order detail
			if not dn_item.so_detail:
				continue

			# Get Shopify line item ID from Sales Order Item
			so_shopify_line_item_id = frappe.db.get_value(
				"Sales Order Item",
				dn_item.so_detail,
				SHOPIFY_LINE_ITEM_ID_FIELD,
			)

			if str(so_shopify_line_item_id or "") != shopify_line_item_id:
				continue

			# Determine allowed return quantity
			allowed_qty = abs(dn_item.qty) if dn_item.qty < 0 else dn_item.qty
			if allowed_qty <= 0:
				continue

			# Set the return quantity (negative for return)
			dn_item.qty = -min(return_qty, allowed_qty)
			dn_item.stock_qty = dn_item.qty * (dn_item.conversion_factor or 1)
			break  # Found match, go to next Shopify line item

	# Remove items with qty 0 to avoid ERPNext error
	return_dn.items = [d for d in return_dn.items if d.qty != 0]


def map_return_si_items(return_si, return_line_items):
	"""
	Map Shopify return_line_items → ERPNext Sales Invoice items
	"""

	return_line_items = deepcopy(return_line_items)

	for r_item in return_line_items:
		fulfillment_line_item = r_item.get("fulfillment_line_item") or {}
		line_item = fulfillment_line_item.get("line_item") or {}

		shopify_line_item_id = str(line_item.get("id"))
		return_qty = cint(r_item.get("quantity"))

		if not shopify_line_item_id or return_qty <= 0:
			continue

		for si_item in return_si.items:
			if not si_item.so_detail:
				continue

			so_shopify_line_item_id = frappe.db.get_value(
				"Sales Order Item",
				si_item.so_detail,
				SHOPIFY_LINE_ITEM_ID_FIELD,
			)

			if str(so_shopify_line_item_id or "") != shopify_line_item_id:
				continue

			allowed_qty = abs(si_item.qty) if si_item.qty < 0 else si_item.qty
			if allowed_qty <= 0:
				continue

			si_item.qty = -min(return_qty, allowed_qty)
			break

	# Remove zero-qty items
	return_si.items = [d for d in return_si.items if d.qty != 0]


def process_invoice_return(payload, request_id=None):
	"""
	Entry point for Shopify returns webhooks
	Handles: open | approved | closed
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	try:
		create_shopify_log(
			status="Debug",
			message=f"Received payload: {frappe.as_json(payload)}",
		)
		shopify_return_id = payload.get("id")

		order_id = payload.get("order_id") or (payload.get("order") or {}).get("id")

		if not shopify_return_id or not order_id:
			create_shopify_log(
				status="Invalid",
				message=payload,
			)
			return

		create_return_sales_invoice(
			payload,
			shopify_return_id,
			order_id,
		)

	except Exception as e:
		create_shopify_log(
			status="Error",
			exception=e,
			rollback=True,
		)


def create_return_sales_invoice(payload, shopify_return_id, order_id):
	"""
	Creates ERPNext Sales Invoice Return (Credit Note)
	"""

	# Prevent duplicate credit notes
	if frappe.db.get_value(
		"Sales Invoice",
		{"shopify_return_id": shopify_return_id},
		"name",
	):
		create_shopify_log(
			status="Ignored",
			message=f"Sales Invoice return already created for {shopify_return_id}",
		)
		return

	si_name = frappe.db.get_value(
		"Sales Invoice",
		{
			ORDER_ID_FIELD: order_id,
			"docstatus": 1,
			"is_return": 0,
		},
		"name",
	)

	if not si_name:
		create_shopify_log(
			status="Invalid",
			message="Original Sales Invoice not found",
		)
		return

	original_si = frappe.get_doc("Sales Invoice", si_name)

	return_si = make_return_doc("Sales Invoice", original_si.name)

	map_return_si_items(
		return_si,
		payload.get("return_line_items") or [],
	)

	if not return_si.items:
		create_shopify_log(
			status="Invalid",
			message="Approved return has no returnable invoice items",
		)
		return

	return_si.shopify_return_id = str(shopify_return_id)
	return_si.flags.ignore_mandatory = True

	return_si.save()
	return_si.submit()

	create_shopify_log(
		status="Success",
		message=f"Return Sales Invoice created: {return_si.name}",
	)
