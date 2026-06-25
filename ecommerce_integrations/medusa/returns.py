# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import json
from collections import defaultdict

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe.utils import cint, cstr, flt

from ecommerce_integrations.medusa.connection import MedusaClient
from ecommerce_integrations.medusa.constants import (
	ORDER_ID_FIELD,
	RETURN_ID_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.medusa.utils import create_medusa_log


def prepare_credit_note(payload, request_id=None):
	"""Create a credit note (return Sales Invoice) for a Medusa return.

	Bound to the ``order.return_received`` topic. The payload is a Medusa
	``return`` dict, or ``{order_id, return}``; if only an id is present it is
	fetched via ``MedusaClient().get_return``. Ports the credit-note + partial
	tax-proration logic from ``unicommerce.cancellation_and_returns``.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	setting = frappe.get_doc(SETTING_DOCTYPE)
	log = frappe.get_doc("Ecommerce Integration Log", request_id) if request_id else None

	try:
		if not cint(setting.sync_returns):
			_finish(log, "Invalid", "Returns sync disabled in Medusa Setting.")
			return

		ret = _resolve_return(payload)
		if not ret:
			_finish(log, "Invalid", "Return not found in payload.")
			return

		return_id = cstr(ret.get("id"))
		order_id = cstr(ret.get("order_id") or payload.get("order_id"))

		# dedup on the credit note's medusa_return_id
		if frappe.db.get_value("Sales Invoice", {RETURN_ID_FIELD: return_id}, "name"):
			_finish(log, "Success", "Credit note already exists for this return.")
			return

		# most recent submitted, non-return SI for the order
		si_name = frappe.db.get_value(
			"Sales Invoice",
			{ORDER_ID_FIELD: order_id, "docstatus": 1, "is_return": 0},
			"name",
			order_by="creation desc",
		)
		if not si_name:
			# the return may arrive before the order/invoice webhooks; ensure them from
			# the full order so the credit note still has an invoice to credit.
			from ecommerce_integrations.medusa.invoice import ensure_sales_invoice

			full_order = MedusaClient().get_order(order_id)
			si_name = ensure_sales_invoice(full_order) if full_order else None
		if not si_name:
			_finish(log, "Invalid", "Sales Invoice not found for syncing credit note.")
			return

		credit_note = create_credit_note(si_name, ret, setting)
		credit_note.set(RETURN_ID_FIELD, return_id)
		credit_note.flags.ignore_mandatory = True
		credit_note.insert(ignore_mandatory=True)
		credit_note.submit()
		_finish(log, "Success")
	except Exception as e:
		create_medusa_log(status="Error", exception=e, rollback=True, request_data=payload)


def _resolve_return(payload):
	"""Pull the Medusa return object out of the payload, fetching it if needed."""
	if not isinstance(payload, dict):
		return None

	ret = payload.get("return") or payload
	# a thin event may only carry ids -> fetch the full return
	# Verified against @medusajs/types 2.4.0.
	if ret and not ret.get("items") and ret.get("id"):
		ret = MedusaClient().get_return(cstr(ret.get("id"))) or ret
	return ret if ret and ret.get("id") else None


def create_credit_note(invoice_name, ret, setting):
	"""Build (unsaved) credit note from the SI, rerouting to the return warehouse,
	negating taxes, and prorating for partial returns."""
	credit_note = make_sales_return(invoice_name)

	return_warehouse = _get_return_warehouse(ret, setting)
	for item in credit_note.items:
		item.warehouse = return_warehouse or item.warehouse

	# make_sales_return already negates tax_amount on the credit note. Older erpnext
	# also carried a per-item ``item_wise_tax_detail`` breakdown; keep it consistent when
	# present. erpnext v16 drops that field, so guard the access (Document.get -> None).
	for tax in credit_note.taxes:
		detail_str = tax.get("item_wise_tax_detail")
		if not detail_str:
			continue
		detail = json.loads(detail_str)
		for _item_code, tax_distribution in detail.items():
			# item_wise_tax_detail value is [rate, amount]
			tax_distribution[1] *= -1
		tax.item_wise_tax_detail = json.dumps(detail)

	returned_items = _get_returned_si_items(credit_note, ret)
	# partial return: only some qty/items were returned
	all_items = {item.sales_invoice_item for item in credit_note.items}
	if returned_items and set(returned_items) != all_items:
		_handle_partial_returns(credit_note, returned_items, ret)

	return credit_note


def _get_return_warehouse(ret, setting):
	"""Resolve the warehouse returned stock lands in.

	Prefer the return location's mapped warehouse, then a ``return_warehouse``
	configured on the Setting, then the default ``warehouse``.
	"""
	location_id = cstr(ret.get("location_id") or "")
	wh_map = setting.get_integration_to_erpnext_wh_mapping()
	return wh_map.get(location_id) or getattr(setting, "return_warehouse", None) or setting.warehouse


def _get_returned_si_items(credit_note, ret):
	"""Map the Medusa return's line items to the credit note's sales_invoice_item rows.

	Medusa return ``items[]`` carry ``item_id`` (the order line item id) which is
	carried onto the SI/credit-note row via the order-line metadata.
	"""
	# item_id -> quantity returned
	# Verified against @medusajs/types 2.4.0.
	returned_line_ids = {cstr(ri.get("item_id")) for ri in (ret.get("items") or [])}
	if not returned_line_ids:
		return []

	matched = []
	for item in credit_note.items:
		line_id = cstr(item.get("medusa_order_line_id") or item.get("ecommerce_item_id") or "")
		if line_id and line_id in returned_line_ids:
			matched.append(item.sales_invoice_item)
	return matched


def _handle_partial_returns(credit_note, returned_items, ret):
	"""Remove non-returned rows and prorate each tax line by the returned-qty ratio.

	Ported from ``unicommerce.cancellation_and_returns._handle_partial_returns``.
	"""
	item_code_to_qty_map = defaultdict(float)
	for item in credit_note.items:
		item_code_to_qty_map[item.item_code] += item.qty

	# apply the per-line returned quantity from the Medusa return where available
	returned_qty_by_line = {cstr(ri.get("item_id")): ri.get("quantity") for ri in (ret.get("items") or [])}

	# remove non-returned items
	credit_note.items = [item for item in credit_note.items if item.sales_invoice_item in returned_items]

	for item in credit_note.items:
		line_id = cstr(item.get("medusa_order_line_id") or item.get("ecommerce_item_id") or "")
		qty = returned_qty_by_line.get(line_id)
		if qty is not None:
			# credit note quantities are negative; preserve sign
			sign = -1 if item.qty < 0 else 1
			item.qty = sign * abs(qty)

	returned_qty_map = defaultdict(float)
	for item in credit_note.items:
		returned_qty_map[item.item_code] += item.qty

	# aggregate returned ratio, used when erpnext doesn't expose a per-item tax breakdown
	total_full = sum(abs(q) for q in item_code_to_qty_map.values()) or 1
	total_returned = sum(abs(q) for q in returned_qty_map.values())
	overall_ratio = total_returned / total_full

	for tax in credit_note.taxes:
		detail_str = tax.get("item_wise_tax_detail")
		if detail_str:
			# older erpnext: prorate each item's tax share by that item's returned ratio
			item_wise_tax_detail = json.loads(detail_str)
			new_tax_amt = 0.0
			for item_code, tax_distribution in item_wise_tax_detail.items():
				# item_code: [rate, amount]
				if not tax_distribution[1]:
					continue
				full_qty = item_code_to_qty_map.get(item_code)
				if not full_qty:
					tax_distribution[1] = 0.0
					continue
				return_percent = returned_qty_map.get(item_code, 0.0) / full_qty
				tax_distribution[1] *= return_percent
				new_tax_amt += tax_distribution[1]
			tax.tax_amount = new_tax_amt
			tax.item_wise_tax_detail = json.dumps(item_wise_tax_detail)
		else:
			# erpnext v16: no per-item breakdown -> prorate the row total by the
			# aggregate returned-quantity ratio.
			tax.tax_amount = flt(tax.tax_amount) * overall_ratio


def _finish(log, status, message=None):
	if log:
		log.status = status
		if message:
			log.message = message
		log.save(ignore_permissions=True)
	elif status != "Success":
		create_medusa_log(status=status, message=message)
