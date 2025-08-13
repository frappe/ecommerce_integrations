import json
from typing import Literal, Optional

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate
from shopify.collection import PaginatedIterator
from shopify.resources import Order

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import (
	CUSTOMER_ID_FIELD,
	EVENT_MAPPER,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.product import create_items_if_not_exist, get_item_code
from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

DEFAULT_TAX_FIELDS = {
	"sales_tax": "default_sales_tax_account",
	"shipping": "default_shipping_charges_account",
}


def sync_sales_order(payload, request_id=None, account=None):
	order = payload
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	# Get account context
	if isinstance(account, str):
		account = frappe.get_doc("Shopify Account", account)
	elif not account:
		# Fallback to legacy mode
		account = frappe.get_doc(SETTING_DOCTYPE)

	if frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: cstr(order["id"])}):
		create_shopify_log(
			status="Invalid", 
			message="Sales order already exists, not synced",
			reference_document=account.name if hasattr(account, 'name') else None
		)
		return
	try:
		shopify_customer = order.get("customer") if order.get("customer") is not None else {}
		shopify_customer["billing_address"] = order.get("billing_address", "")
		shopify_customer["shipping_address"] = order.get("shipping_address", "")
		customer_id = shopify_customer.get("id")
		if customer_id:
			customer = ShopifyCustomer(customer_id=customer_id, account=account)
			if not customer.is_synced():
				customer.sync_customer(customer=shopify_customer)
			else:
				customer.update_existing_addresses(shopify_customer)

		create_items_if_not_exist(order, account)

		create_order(order, account)
	except Exception as e:
		create_shopify_log(
			status="Error", 
			exception=e, 
			rollback=True,
			reference_document=account.name if hasattr(account, 'name') else None
		)
	else:
		create_shopify_log(
			status="Success",
			reference_document=account.name if hasattr(account, 'name') else None
		)


def create_order(order, account, company=None):
	# local import to avoid circular dependencies
	from ecommerce_integrations.shopify.fulfillment import create_delivery_note
	from ecommerce_integrations.shopify.invoice import create_sales_invoice

	so = create_sales_order(order, account, company)
	if so:
		if order.get("financial_status") == "paid" and _should_sync_invoice(account):
			create_sales_invoice(order, account, so)

		if order.get("fulfillments") and _should_sync_delivery_note(account):
			create_delivery_note(order, account, so)


def create_sales_order(shopify_order, account, company=None):
	customer = _get_default_customer(account)
	if shopify_order.get("customer", {}):
		if customer_id := shopify_order.get("customer", {}).get("id"):
			customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name")

	so = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")

	if not so:
		items = get_order_items(
			shopify_order.get("line_items"),
			account,
			getdate(shopify_order.get("created_at")),
			taxes_inclusive=shopify_order.get("taxes_included"),
		)

		if not items:
			message = (
				"Following items exists in the shopify order but relevant records were"
				" not found in the shopify Product master"
			)
			product_not_exists = []  # TODO: fix missing items
			message += "\n" + ", ".join(product_not_exists)

			create_shopify_log(status="Error", exception=message, rollback=True)

			return ""

		taxes = get_order_taxes(shopify_order, account, items)
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"naming_series": _get_sales_order_series(account),
				ORDER_ID_FIELD: str(shopify_order.get("id")),
				ORDER_NUMBER_FIELD: shopify_order.get("name"),
				"customer": customer,
				"transaction_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"delivery_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"company": _get_company(account),
				"selling_price_list": _get_selling_price_list(account),
				"ignore_pricing_rule": 1,
				"items": items,
				"taxes": taxes,
				"tax_category": get_dummy_tax_category(),
			}
		)

		if company:
			so.update({"company": company, "status": "Draft"})
		so.flags.ignore_mandatory = True
		so.flags.shopiy_order_json = json.dumps(shopify_order)
		so.save(ignore_permissions=True)
		so.submit()

		if shopify_order.get("note"):
			so.add_comment(text=f"Order Note: {shopify_order.get('note')}")

	else:
		so = frappe.get_doc("Sales Order", so)

	return so


def get_order_items(order_items, account, delivery_date, taxes_inclusive):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append(
				{"title": shopify_item.get("title"), ORDER_ID_FIELD: shopify_item.get("id")}
			)
			continue

		if all_product_exists:
			item_code = get_item_code(shopify_item)
			items.append(
				{
					"item_code": item_code,
					"item_name": shopify_item.get("name"),
					"rate": _get_item_price(shopify_item, taxes_inclusive),
					"delivery_date": delivery_date,
					"qty": shopify_item.get("quantity"),
					"stock_uom": shopify_item.get("uom") or "Nos",
					"warehouse": _get_default_warehouse_for_order(account),
					ORDER_ITEM_DISCOUNT_FIELD: (
						_get_total_discount(shopify_item) / cint(shopify_item.get("quantity"))
					),
				}
			)
		else:
			items = []

	return items


def _get_item_price(line_item, taxes_inclusive: bool) -> float:
	price = flt(line_item.get("price"))
	qty = cint(line_item.get("quantity"))

	# remove line item level discounts
	total_discount = _get_total_discount(line_item)

	if not taxes_inclusive:
		return price - (total_discount / qty)

	total_taxes = 0.0
	for tax in line_item.get("tax_lines"):
		total_taxes += flt(tax.get("price"))

	return price - (total_taxes + total_discount) / qty


def _get_total_discount(line_item) -> float:
	discount_allocations = line_item.get("discount_allocations") or []
	return sum(flt(discount.get("amount")) for discount in discount_allocations)


def get_order_taxes(shopify_order, setting, items):
	taxes = []
	line_items = shopify_order.get("line_items")

	for line_item in line_items:
		item_code = get_item_code(line_item)
		for tax in line_item.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax, charge_type="sales_tax"),
					"description": (
						get_tax_account_description(tax)
						or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": tax.get("price"),
					"included_in_print_rate": 0,
					"cost_center": setting.cost_center,
					"item_wise_tax_detail": {item_code: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]},
					"dont_recompute_tax": 1,
				}
			)

	update_taxes_with_shipping_lines(
		taxes,
		shopify_order.get("shipping_lines"),
		setting,
		items,
		taxes_inclusive=shopify_order.get("taxes_included"),
	)

	if cint(setting.consolidate_taxes):
		taxes = consolidate_order_taxes(taxes)

	for row in taxes:
		tax_detail = row.get("item_wise_tax_detail")
		if isinstance(tax_detail, dict):
			row["item_wise_tax_detail"] = json.dumps(tax_detail)

	return taxes


def consolidate_order_taxes(taxes):
	tax_account_wise_data = {}
	for tax in taxes:
		account_head = tax["account_head"]
		tax_account_wise_data.setdefault(
			account_head,
			{
				"charge_type": "Actual",
				"account_head": account_head,
				"description": tax.get("description"),
				"cost_center": tax.get("cost_center"),
				"included_in_print_rate": 0,
				"dont_recompute_tax": 1,
				"tax_amount": 0,
				"item_wise_tax_detail": {},
			},
		)
		tax_account_wise_data[account_head]["tax_amount"] += flt(tax.get("tax_amount"))
		if tax.get("item_wise_tax_detail"):
			tax_account_wise_data[account_head]["item_wise_tax_detail"].update(tax["item_wise_tax_detail"])

	return tax_account_wise_data.values()


def get_tax_account_head(tax, charge_type: Literal["shipping", "sales_tax"] | None = None):
	tax_title = str(tax.get("title"))

	tax_account = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": SETTING_DOCTYPE, "shopify_tax": tax_title},
		"tax_account",
	)

	if not tax_account and charge_type:
		tax_account = frappe.db.get_single_value(SETTING_DOCTYPE, DEFAULT_TAX_FIELDS[charge_type])

	if not tax_account:
		frappe.throw(_("Tax Account not specified for Shopify Tax {0}").format(tax.get("title")))

	return tax_account


def get_tax_account_description(tax):
	tax_title = tax.get("title")

	tax_description = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": SETTING_DOCTYPE, "shopify_tax": tax_title},
		"tax_description",
	)

	return tax_description


def update_taxes_with_shipping_lines(taxes, shipping_lines, setting, items, taxes_inclusive=False):
	"""Shipping lines represents the shipping details,
	each such shipping detail consists of a list of tax_lines"""
	shipping_as_item = cint(setting.add_shipping_as_item) and setting.shipping_item
	for shipping_charge in shipping_lines:
		if shipping_charge.get("price"):
			shipping_discounts = shipping_charge.get("discount_allocations") or []
			total_discount = sum(flt(discount.get("amount")) for discount in shipping_discounts)

			shipping_taxes = shipping_charge.get("tax_lines") or []
			total_tax = sum(flt(discount.get("price")) for discount in shipping_taxes)

			shipping_charge_amount = flt(shipping_charge["price"]) - flt(total_discount)
			if bool(taxes_inclusive):
				shipping_charge_amount -= total_tax

			if shipping_as_item:
				items.append(
					{
						"item_code": setting.shipping_item,
						"rate": shipping_charge_amount,
						"delivery_date": items[-1]["delivery_date"] if items else nowdate(),
						"qty": 1,
						"stock_uom": "Nos",
						"warehouse": setting.warehouse,
					}
				)
			else:
				taxes.append(
					{
						"charge_type": "Actual",
						"account_head": get_tax_account_head(shipping_charge, charge_type="shipping"),
						"description": get_tax_account_description(shipping_charge)
						or shipping_charge["title"],
						"tax_amount": shipping_charge_amount,
						"cost_center": setting.cost_center,
					}
				)

		for tax in shipping_charge.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax, charge_type="sales_tax"),
					"description": (
						get_tax_account_description(tax)
						or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": tax["price"],
					"cost_center": setting.cost_center,
					"item_wise_tax_detail": {
						setting.shipping_item: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]
					}
					if shipping_as_item
					else {},
					"dont_recompute_tax": 1,
				}
			)


def get_sales_order(order_id):
	"""Get ERPNext sales order using shopify order id."""
	sales_order = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: order_id})
	if sales_order:
		return frappe.get_doc("Sales Order", sales_order)


def cancel_order(payload, request_id=None, account=None):
	"""Called by order/cancelled event.

	When shopify order is cancelled there could be many different someone handles it.

	Updates document with custom field showing order status.

	IF sales invoice / delivery notes are not generated against an order, then cancel it.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	# Get account context
	if isinstance(account, str):
		account = frappe.get_doc("Shopify Account", account)
	elif not account:
		# Fallback to legacy mode
		account = frappe.get_doc(SETTING_DOCTYPE)

	order = payload

	try:
		order_id = order["id"]
		order_status = order["financial_status"]

		sales_order = get_sales_order(order_id)

		if not sales_order:
			create_shopify_log(
				status="Invalid", 
				message="Sales Order does not exist",
				reference_document=account.name if hasattr(account, 'name') else None
			)
			return

		sales_invoice = frappe.db.get_value("Sales Invoice", filters={ORDER_ID_FIELD: order_id})
		delivery_notes = frappe.db.get_list("Delivery Note", filters={ORDER_ID_FIELD: order_id})

		if sales_invoice:
			frappe.db.set_value("Sales Invoice", sales_invoice, ORDER_STATUS_FIELD, order_status)

		for dn in delivery_notes:
			frappe.db.set_value("Delivery Note", dn.name, ORDER_STATUS_FIELD, order_status)

		if not sales_invoice and not delivery_notes and sales_order.docstatus == 1:
			sales_order.cancel()
		else:
			frappe.db.set_value("Sales Order", sales_order.name, ORDER_STATUS_FIELD, order_status)

	except Exception as e:
		create_shopify_log(
			status="Error", 
			exception=e,
			reference_document=account.name if hasattr(account, 'name') else None
		)
	else:
		create_shopify_log(
			status="Success",
			reference_document=account.name if hasattr(account, 'name') else None
		)


@temp_shopify_session
def sync_old_orders(account=None):
	"""Sync old orders for specific account or all enabled accounts."""
	if account:
		# Sync for specific account
		_sync_old_orders_for_account(account)
	else:
		# Sync for all enabled accounts
		enabled_accounts = frappe.get_all(ACCOUNT_DOCTYPE, 
			filters={"enabled": 1}, 
			fields=["name"])
		
		if not enabled_accounts:
			# Fallback to legacy singleton
			_sync_old_orders_legacy()
			return
		
		for account_data in enabled_accounts:
			account_doc = frappe.get_doc(ACCOUNT_DOCTYPE, account_data.name)
			_sync_old_orders_for_account(account_doc)

def _sync_old_orders_legacy():
	"""Legacy old order sync using singleton."""
	shopify_setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not cint(shopify_setting.sync_old_orders):
		return

	orders = _fetch_old_orders(shopify_setting.old_orders_from, shopify_setting.old_orders_to)

	for order in orders:
		log = create_shopify_log(
			method=EVENT_MAPPER["orders/create"], request_data=json.dumps(order), make_new=True
		)
		sync_sales_order(order, request_id=log.name)

	shopify_setting = frappe.get_doc(SETTING_DOCTYPE)
	shopify_setting.sync_old_orders = 0
	shopify_setting.save()

def _sync_old_orders_for_account(account):
	"""Sync old orders for a specific Shopify account."""
	if not account.is_enabled():
		return
	
	# Check if account has old order sync enabled
	# TODO: Add old_orders_sync fields to Shopify Account doctype
	# For now, we'll assume accounts don't need old order sync by default
	# This should be controlled by account-specific settings
	
	# Placeholder for account-specific old order sync logic
	# This would need additional fields in Shopify Account doctype:
	# - sync_old_orders (Check)
	# - old_orders_from (Datetime) 
	# - old_orders_to (Datetime)
	
	if not hasattr(account, 'sync_old_orders') or not cint(account.sync_old_orders):
		return
	
	# Use account-specific session
	with temp_shopify_session(account=account):
		orders = _fetch_old_orders(
			getattr(account, 'old_orders_from', None), 
			getattr(account, 'old_orders_to', None)
		)

		for order in orders:
			log = create_shopify_log(
				method=EVENT_MAPPER["orders/create"], 
				request_data=json.dumps(order), 
				make_new=True,
				reference_document=account.name
			)
			sync_sales_order(order, request_id=log.name, account=account)

		# Update account sync status
		account.sync_old_orders = 0
		account.save()


def _fetch_old_orders(from_time, to_time):
	"""Fetch all shopify orders in specified range and return an iterator on fetched orders."""

	from_time = get_datetime(from_time).astimezone().isoformat()
	to_time = get_datetime(to_time).astimezone().isoformat()
	orders_iterator = PaginatedIterator(
		Order.find(created_at_min=from_time, created_at_max=to_time, limit=250)
	)

	for orders in orders_iterator:
		for order in orders:
			# Using generator instead of fetching all at once is better for
			# avoiding rate limits and reducing resource usage.
			yield order.to_dict()


# Helper functions for account-aware integration

def _get_default_customer(account):
	"""Get default customer from account or legacy setting."""
	if hasattr(account, 'default_customer') and account.default_customer:
		return account.default_customer
	elif hasattr(account, 'default_customer'):  # Shopify Account
		return account.default_customer
	else:  # Legacy Shopify Setting
		return account.default_customer

def _get_company(account):
	"""Get company from account or legacy setting."""
	return account.company

def _get_sales_order_series(account):
	"""Get sales order series from account or legacy setting."""
	if hasattr(account, 'sales_order_series'):
		return account.sales_order_series or "SO-Shopify-"
	else:  # Legacy setting
		return account.sales_order_series or "SO-Shopify-"

def _get_selling_price_list(account):
	"""Get selling price list from account or fallback to dummy."""
	if hasattr(account, 'selling_price_list') and account.selling_price_list:
		return account.selling_price_list
	return get_dummy_price_list()

def _should_sync_invoice(account):
	"""Check if sales invoice sync is enabled for this account."""
	if hasattr(account, 'sync_sales_invoice'):
		return bool(account.sync_sales_invoice)
	else:  # Legacy setting
		return bool(account.sync_sales_invoice)

def _should_sync_delivery_note(account):
	"""Check if delivery note sync is enabled for this account."""
	if hasattr(account, 'sync_delivery_note'):
		return bool(account.sync_delivery_note)
	else:  # Legacy setting
		return bool(account.sync_delivery_note)

def _get_default_warehouse_for_order(account):
	"""Get default warehouse for order items from account or legacy setting."""
	if hasattr(account, 'warehouse'):
		return account.warehouse
	elif hasattr(account, 'warehouse_mappings') and account.warehouse_mappings:
		# Use first warehouse as default for new account
		return account.warehouse_mappings[0].erpnext_warehouse
	else:  # Legacy setting
		return account.warehouse
