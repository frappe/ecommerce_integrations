# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from frappe import _
from frappe.utils import cint, cstr, getdate

from ecommerce_integrations.medusa.constants import (
	FULFILLMENT_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_LINE_ID_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.medusa.utils import create_medusa_log, parse_datetime


def prepare_delivery_note(payload, request_id=None):
	"""Create a Delivery Note for each Medusa fulfillment on an order.

	Mirrors ``shopify.fulfillment.prepare_delivery_note``. Bound to the
	``fulfillment.created`` topic. The payload references a Medusa ``order``
	(carrying its ``fulfillments``); a DN is created per not-yet-synced
	fulfillment, deduped on ``FULFILLMENT_ID_FIELD``.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	# order.fulfillment_created carries {order_id, fulfillment_id}; resolve to the
	# full order (with its fulfillments) so create_delivery_note can iterate them.
	order = _resolve_order(payload)

	setting = frappe.get_doc(SETTING_DOCTYPE)
	log = frappe.get_doc("Ecommerce Integration Log", request_id) if request_id else None

	try:
		if not cint(setting.sync_delivery_note):
			_finish(log, "Invalid", "Delivery Note sync disabled in Medusa Setting.")
			return

		order_id = cstr(order.get("id"))
		so_name = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: order_id, "docstatus": 1}, "name")
		if not so_name:
			_finish(log, "Invalid", "Sales Order not found for syncing delivery note.")
			return

		so = frappe.get_doc("Sales Order", so_name)
		create_delivery_note(order, setting, so)
		_finish(log, "Success")
	except Exception as e:
		create_medusa_log(status="Error", exception=e, rollback=True, request_data=payload)


def create_delivery_note(order, setting, so):
	if not cint(setting.sync_delivery_note) or so.docstatus != 1:
		return

	order_id = cstr(order.get("id"))

	for fulfillment in order.get("fulfillments") or []:
		fulfillment_id = cstr(fulfillment.get("id"))

		# dedup on the DN's medusa_fulfillment_id
		if frappe.db.get_value("Delivery Note", {FULFILLMENT_ID_FIELD: fulfillment_id}, "name"):
			continue

		dn = make_delivery_note(so.name)
		dn.set(ORDER_ID_FIELD, order_id)
		dn.set(ORDER_NUMBER_FIELD, cstr(order.get("display_id")))
		dn.set(ORDER_STATUS_FIELD, order.get("status"))
		dn.set(FULFILLMENT_ID_FIELD, fulfillment_id)
		dn.set_posting_time = 1
		# fulfillment timeline: prefer shipped_at, fall back to packed_at
		# Verified against @medusajs/types 2.4.0.
		posting = parse_datetime(fulfillment.get("shipped_at")) or parse_datetime(
			fulfillment.get("packed_at")
		)
		if posting:
			dn.posting_date = getdate(posting)
		dn.naming_series = setting.delivery_note_series or "DN-Medusa-"
		dn.items = get_fulfillment_items(dn.items, fulfillment, setting)
		dn.flags.ignore_mandatory = True
		dn.save()
		dn.submit()


def get_fulfillment_items(dn_items, fulfillment, setting):
	"""Filter the DN rows down to the fulfillment's line items and set qty + warehouse.

	A Medusa fulfillment carries ``items[]{line_item_id, quantity}`` and a
	``location_id``; the location maps to an ERPNext warehouse via the Setting.
	"""
	wh_map = setting.get_integration_to_erpnext_wh_mapping()
	warehouse = wh_map.get(cstr(fulfillment.get("location_id"))) or setting.warehouse

	# line_item_id -> quantity from the fulfillment (line_item_id is the Medusa order
	# line id, stamped on the DN row as medusa_order_line_id). Verified against
	# @medusajs/types 2.4.0.
	qty_by_line = {
		cstr(fi.get("line_item_id")): fi.get("quantity") for fi in (fulfillment.get("items") or [])
	}

	final_items = []
	for dn_item in dn_items:
		# match strictly on the Medusa order line id stamped on the DN row
		line_id = cstr(dn_item.get(ORDER_LINE_ID_FIELD) or "")
		qty = qty_by_line.get(line_id)

		if qty is None:
			continue

		dn_item.qty = qty
		dn_item.warehouse = warehouse
		final_items.append(dn_item)

	# fail loudly rather than ship arbitrary stock when no line matched (missing metadata)
	if not final_items:
		frappe.throw(
			_("Could not match Medusa fulfillment {0} items to the Delivery Note").format(
				cstr(fulfillment.get("id"))
			)
		)

	return final_items


def _resolve_order(payload):
	"""Normalise a delivery-note payload to a Medusa order dict.

	The companion subscriber forwards the full order for order.fulfillment_created, but
	resolve defensively so a raw {order_id, fulfillment_id} event, an {order} wrapper, or
	a poll/backfill order all work. Fetching the full order is safe: its fulfillments are
	deduped on medusa_fulfillment_id, so already-synced ones are skipped.
	"""
	if not isinstance(payload, dict):
		return payload
	if payload.get("fulfillments"):
		return payload
	inner = payload.get("order")
	if isinstance(inner, dict) and inner.get("fulfillments"):
		return inner
	order_id = payload.get("order_id") or payload.get("id")
	if not order_id and isinstance(inner, dict):
		order_id = inner.get("id")
	if order_id:
		from ecommerce_integrations.medusa.connection import MedusaClient

		return MedusaClient().get_order(cstr(order_id))
	return payload


def _finish(log, status, message=None):
	if log:
		log.status = status
		if message:
			log.message = message
		log.save(ignore_permissions=True)
	elif status != "Success":
		create_medusa_log(status=status, message=message)
