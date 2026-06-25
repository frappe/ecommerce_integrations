# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate

from ecommerce_integrations.medusa.connection import MedusaClient
from ecommerce_integrations.medusa.constants import (
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_LINE_ID_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.medusa.customer import MedusaCustomer
from ecommerce_integrations.medusa.utils import create_medusa_log, parse_datetime, to_amount
from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category


def sync_sales_order(payload, request_id=None):
	"""Create an ERPNext Sales Order from a Medusa ``order.placed`` event.

	``payload`` may be the full order dict (the subscriber ships the whole order) OR a
	thin event carrying just an id — in the latter case (no ``items``) we fetch the full
	order from the Admin API. Mirrors ``shopify.order.sync_sales_order``.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	log = frappe.get_doc("Ecommerce Integration Log", request_id) if request_id else None

	try:
		order = payload

		# thin event → fetch the full order
		if not order.get("items"):
			order_id = order.get("id") or order.get("order_id")
			order = MedusaClient().get_order(order_id)

		order_id = cstr(order.get("id"))

		# dedup
		if frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: order_id}):
			_finish(log, "Invalid", "Sales order already exists, not synced")
			return

		# local import to avoid circular dependency (product imports order helpers)
		from ecommerce_integrations.medusa.product import create_items_if_not_exist

		create_items_if_not_exist(order)

		medusa_customer = order.get("customer") or {}
		customer_id = order.get("customer_id") or medusa_customer.get("id")
		if customer_id and medusa_customer:
			# attach the per-order address snapshots so the customer sync can build Addresses
			medusa_customer = dict(medusa_customer)
			medusa_customer["billing_address"] = order.get("billing_address") or {}
			medusa_customer["shipping_address"] = order.get("shipping_address") or {}

			customer = MedusaCustomer(customer_id)
			if not customer.is_synced():
				customer.sync_customer(medusa_customer)

		setting = frappe.get_doc(SETTING_DOCTYPE)
		create_sales_order(order, setting)
	except Exception as e:
		create_medusa_log(status="Error", exception=e, rollback=True, request_data=payload)
	else:
		_finish(log, "Success")


def _finish(log, status, message=None):
	"""Set status on the reused log, or open a fresh one if there is none."""
	if log:
		log.status = status
		if message:
			log.message = message
		log.save(ignore_permissions=True)
	else:
		create_medusa_log(status=status, message=message)


def create_sales_order(order, setting):
	"""Build and submit an ERPNext Sales Order from a Medusa order dict."""
	so = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: cstr(order.get("id"))}, "name")
	if so:
		return frappe.get_doc("Sales Order", so)

	customer = setting.default_customer
	customer_id = order.get("customer_id") or (order.get("customer") or {}).get("id")
	if customer_id:
		synced = frappe.db.get_value("Customer", {"medusa_customer_id": customer_id}, "name")
		if synced:
			customer = synced

	created_at = parse_datetime(order.get("created_at"))
	transaction_date = getdate(created_at) if created_at else nowdate()

	items = get_order_items(order, setting)
	if not items:
		create_medusa_log(
			status="Error",
			message="No syncable line items found on Medusa order",
			rollback=True,
			request_data=order,
		)
		return ""

	taxes = get_order_taxes(order, setting)

	selling_price_list = (
		setting.selling_price_list if cint(setting.use_price_list) else get_dummy_price_list()
	)

	so = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"naming_series": setting.sales_order_series or "SO-Medusa-",
			ORDER_ID_FIELD: cstr(order.get("id")),
			ORDER_NUMBER_FIELD: cstr(order.get("display_id")),
			ORDER_STATUS_FIELD: order.get("status"),
			"customer": customer,
			"transaction_date": transaction_date,
			"delivery_date": transaction_date,
			"company": setting.company,
			"currency": order.get("currency_code"),
			"selling_price_list": selling_price_list,
			"ignore_pricing_rule": 1,
			"tax_category": get_dummy_tax_category(),
			"items": items,
			"taxes": taxes,
		}
	)

	so.flags.ignore_mandatory = True
	so.save(ignore_permissions=True)
	so.submit()

	return so


def get_order_items(order, setting) -> list[dict]:
	"""Map Medusa order line items to ERPNext Sales Order Item rows."""
	# local import to avoid circular dependency
	from ecommerce_integrations.medusa.product import get_item_code

	items = []
	warehouse = setting.warehouse

	created_at = parse_datetime(order.get("created_at"))
	delivery_date = getdate(created_at) if created_at else nowdate()

	for line in order.get("items") or []:
		item_code = get_item_code(line)
		qty = cint(line.get("quantity")) or 1

		# Medusa's unit_price is pre-discount and discount_total is the line discount; net
		# the per-unit discount into the rate so ERPNext totals reflect it (a read-only
		# custom field keeps the discount for reference). Verified against @medusajs/types 2.4.0.
		unit_price = to_amount(line.get("unit_price"))
		discount_total = to_amount(line.get("discount_total"))
		discount_per_unit = discount_total / qty if qty else discount_total

		items.append(
			{
				"item_code": item_code,
				"item_name": line.get("title") or line.get("product_title"),
				"rate": unit_price - discount_per_unit,
				"qty": qty,
				"stock_uom": "Nos",
				"delivery_date": delivery_date,
				"warehouse": warehouse,
				ORDER_ITEM_DISCOUNT_FIELD: discount_per_unit,
				ORDER_LINE_ID_FIELD: cstr(line.get("id")),
			}
		)

	# shipping as a line item (the Actual-charge path lives in get_order_taxes / _add_shipping)
	shipping_total = _get_shipping_total(order)
	if cint(setting.add_shipping_as_item) and setting.shipping_item and shipping_total:
		items.append(
			{
				"item_code": setting.shipping_item,
				"item_name": _("Shipping"),
				"rate": shipping_total,
				"qty": 1,
				"stock_uom": "Nos",
				"delivery_date": delivery_date,
				"warehouse": warehouse,
			}
		)

	return items


def _get_shipping_total(order) -> float:
	"""Total shipping charge on a Medusa order (a total, or summed shipping_methods)."""
	shipping_total = to_amount(order.get("shipping_total"))  # Verified against @medusajs/types 2.4.0.
	if not shipping_total:
		shipping_total = sum(to_amount(m.get("amount")) for m in (order.get("shipping_methods") or []))
	return shipping_total


def get_order_taxes(order, setting) -> list[dict]:
	"""Map Medusa order taxes + shipping to ERPNext Sales Taxes and Charges rows."""
	# local import to avoid circular dependency
	from ecommerce_integrations.medusa.product import get_item_code

	taxes = []

	for line in order.get("items") or []:
		tax_amount = to_amount(line.get("tax_total"))  # Verified against @medusajs/types 2.4.0.
		if not tax_amount:
			continue
		item_code = get_item_code(line)
		# Medusa line items don't carry a named tax rate at the order snapshot level;
		# fall back to the default sales-tax account mapping.
		taxes.append(
			{
				"charge_type": "Actual",
				"account_head": _get_tax_account(setting, line.get("title"), "sales_tax"),
				"description": _get_tax_description(setting, line.get("title")) or _("Sales Tax"),
				"tax_amount": tax_amount,
				"cost_center": setting.cost_center,
				"included_in_print_rate": 0,
				"dont_recompute_tax": 1,
				"item_wise_tax_detail": {item_code: [0.0, tax_amount]},
			}
		)

	_add_shipping(taxes, order, setting)

	if cint(setting.consolidate_taxes):
		taxes = _consolidate_taxes(taxes)

	for row in taxes:
		detail = row.get("item_wise_tax_detail")
		if isinstance(detail, dict):
			row["item_wise_tax_detail"] = json.dumps(detail)

	return taxes


def _add_shipping(taxes, order, setting):
	"""Add the order's shipping charge as either an Actual tax row or an item line.

	When ``add_shipping_as_item`` is set the caller would normally append a line item;
	since ``get_order_items`` runs separately we add the shipping *item* directly to the
	Sales Order via taxes is not possible — so for the item case we leave it to the
	default shipping account unless the operator opts into the item flow elsewhere.
	"""
	shipping_total = _get_shipping_total(order)
	if not shipping_total:
		return

	if cint(setting.add_shipping_as_item) and setting.shipping_item:
		# shipping is added as a line item in get_order_items; nothing to add here
		return

	shipping_name = None
	methods = order.get("shipping_methods") or []
	if methods:
		shipping_name = methods[0].get("name")

	taxes.append(
		{
			"charge_type": "Actual",
			"account_head": _get_tax_account(setting, shipping_name, "shipping"),
			"description": _get_tax_description(setting, shipping_name) or shipping_name or _("Shipping"),
			"tax_amount": shipping_total,
			"cost_center": setting.cost_center,
			"included_in_print_rate": 0,
		}
	)


def _get_tax_account(setting, title, charge_type):
	"""Resolve a Medusa tax title → ERPNext account via setting.taxes, else the default."""
	account = None
	if title:
		for row in setting.taxes or []:
			if row.get("medusa_tax") == title:
				account = row.get("tax_account")
				break

	if not account:
		if charge_type == "shipping":
			account = setting.default_shipping_charges_account
		else:
			account = setting.default_sales_tax_account

	if not account:
		frappe.throw(_("Tax/Charge account not configured for Medusa {0}").format(title or charge_type))

	return account


def _get_tax_description(setting, title):
	if not title:
		return None
	for row in setting.taxes or []:
		if row.get("medusa_tax") == title:
			return row.get("tax_description")
	return None


def _consolidate_taxes(taxes):
	"""Merge multiple Actual rows that hit the same account head into one."""
	by_account = {}
	for tax in taxes:
		account_head = tax["account_head"]
		by_account.setdefault(
			account_head,
			{
				"charge_type": "Actual",
				"account_head": account_head,
				"description": tax.get("description"),
				"cost_center": tax.get("cost_center"),
				"included_in_print_rate": 0,
				"dont_recompute_tax": 1,
				"tax_amount": 0.0,
				"item_wise_tax_detail": {},
			},
		)
		by_account[account_head]["tax_amount"] += flt(tax.get("tax_amount"))
		detail = tax.get("item_wise_tax_detail")
		if isinstance(detail, dict):
			by_account[account_head]["item_wise_tax_detail"].update(detail)

	return list(by_account.values())


def get_sales_order(order_id):
	"""Get the ERPNext Sales Order for a Medusa order id."""
	name = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: cstr(order_id)})
	if name:
		return frappe.get_doc("Sales Order", name)


def ensure_sales_order(order):
	"""Return the submitted Sales Order for a Medusa order, creating it first if it is
	missing (e.g. a downstream webhook processed before order.placed). Idempotent.
	"""
	filters = {ORDER_ID_FIELD: cstr(order.get("id")), "docstatus": 1}
	name = frappe.db.get_value("Sales Order", filters, "name")
	if not name:
		sync_sales_order(order)
		frappe.db.commit()
		name = frappe.db.get_value("Sales Order", filters, "name")
	return frappe.get_doc("Sales Order", name) if name else None


def cancel_order(payload, request_id=None):
	"""Handle a Medusa ``order.canceled`` event.

	If no submitted Sales Invoice / Delivery Note is linked, cancel the Sales Order;
	otherwise just stamp the Medusa order status on the SO/SI/DN. Mirrors
	``shopify.order.cancel_order``.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	log = frappe.get_doc("Ecommerce Integration Log", request_id) if request_id else None
	order = payload

	try:
		order_id = cstr(order.get("id"))
		order_status = order.get("status") or "canceled"

		# ensure the SO exists even if order.canceled processed before order.placed
		sales_order = ensure_sales_order(order)
		if not sales_order:
			_finish(log, "Invalid", "Sales Order does not exist")
			return

		sales_invoice = frappe.db.get_value(
			"Sales Invoice", {ORDER_ID_FIELD: order_id, "docstatus": 1}, "name"
		)
		delivery_notes = frappe.db.get_list(
			"Delivery Note", filters={ORDER_ID_FIELD: order_id, "docstatus": 1}
		)

		if sales_invoice:
			frappe.db.set_value("Sales Invoice", sales_invoice, ORDER_STATUS_FIELD, order_status)
		for dn in delivery_notes:
			frappe.db.set_value("Delivery Note", dn.name, ORDER_STATUS_FIELD, order_status)

		if not sales_invoice and not delivery_notes and sales_order.docstatus == 1:
			sales_order.cancel()
		else:
			frappe.db.set_value("Sales Order", sales_order.name, ORDER_STATUS_FIELD, order_status)
	except Exception as e:
		create_medusa_log(status="Error", exception=e, request_data=payload)
	else:
		_finish(log, "Success")


def sync_old_orders():
	"""Scheduled backfill of historical Medusa orders within a configured window."""
	setting = frappe.get_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not cint(setting.sync_old_orders):
		return

	from_time = get_datetime(setting.old_orders_from).astimezone().isoformat()
	to_time = get_datetime(setting.old_orders_to).astimezone().isoformat()

	# Medusa v2 filter operators are "$"-prefixed (OperatorMap: $gte/$lte), rendered
	# as nested query params. Verified against @medusajs/types 2.4.0.
	params = {"updated_at[$gte]": from_time, "updated_at[$lte]": to_time}

	for order in MedusaClient().list_orders(params):
		log = create_medusa_log(
			method="ecommerce_integrations.medusa.order.sync_sales_order",
			request_data=order,
			make_new=True,
		)
		sync_sales_order(order, request_id=log.name)
		frappe.db.commit()  # persist the SO first so a replay failure can't roll it back
		# replay downstream state that won't arrive as live webhooks for historical orders
		_replay_order_state(order)

	frappe.db.set_value(SETTING_DOCTYPE, None, "sync_old_orders", 0)


def _replay_order_state(order):
	"""Replay a backfilled order's paid / fulfilled / canceled state.

	Live orders get their Sales Invoice, Payment Entry and Delivery Note from webhook
	events; historical orders pulled by the backfill won't, so drive those handlers
	directly here. Each is gated by its Setting toggle and dedups, so this is safe and
	idempotent.
	"""
	status = (order.get("status") or "").lower()
	if status in ("canceled", "cancelled"):
		cancel_order(order)
		return

	from ecommerce_integrations.medusa.invoice import prepare_sales_invoice

	# prepare_sales_invoice gates on a captured payment internally
	prepare_sales_invoice(order)
	frappe.db.commit()

	if order.get("fulfillments"):
		from ecommerce_integrations.medusa.fulfillment import prepare_delivery_note

		prepare_delivery_note(order)
		frappe.db.commit()
