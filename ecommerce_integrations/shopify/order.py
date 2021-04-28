import frappe
from frappe import _
from frappe.utils import cstr, nowdate, getdate, flt

from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.product import (
	create_items_if_not_exist,
	get_item_code,
)
from ecommerce_integrations.shopify.invoice import create_sales_invoice
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE


def sync_sales_order(order, request_id=None):
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	if not frappe.db.get_value(
		"Sales Order", filters={"shopify_order_id": cstr(order["id"])}
	):
		try:
			shopify_customer = order.get("customer", {})
			customer = ShopifyCustomer(customer_id=shopify_customer.get("id"))

			if not customer.is_synced():
				customer.sync_customer(customer=shopify_customer)

			create_items_if_not_exist(order)

			shopify_setting = frappe.get_doc(SETTING_DOCTYPE)
			create_order(order, shopify_setting)
		except Exception as e:
			create_shopify_log(status="Error", exception=e)
		else:
			create_shopify_log(status="Success")


def create_order(order, shopify_settings, company=None):
	so = create_sales_order(order, shopify_settings, company)
	if so:
		if order.get("financial_status") == "paid":
			create_sales_invoice(order, shopify_settings, so)

		if order.get("fulfillments"):
			# create_delivery_note(order, shopify_settings, so)
			pass  # TODO


def create_sales_order(shopify_order, shopify_settings, company=None):
	product_not_exists = []
	customer = frappe.db.get_value(
		"Customer",
		{"shopify_customer_id": shopify_order.get("customer", {}).get("id")},
		"name",
	)
	so = frappe.db.get_value(
		"Sales Order", {"shopify_order_id": shopify_order.get("id")}, "name"
	)

	if not so:
		items = get_order_items(
			shopify_order.get("line_items"),
			shopify_settings,
			getdate(shopify_order.get("created_at")),
		)

		if not items:
			message = (
				"Following items exists in the shopify order but relevant records were"
				" not found in the shopify Product master"
			)
			message += "\n" + ", ".join(product_not_exists)

			create_shopify_log(status="Error", exception=message, rollback=True)

			return ""

		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"naming_series": shopify_settings.sales_order_series or "SO-Shopify-",
				"shopify_order_id": shopify_order.get("id"),
				"shopify_order_number": shopify_order.get("name"),
				"customer": customer or shopify_settings.default_customer,
				"transaction_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"delivery_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"company": shopify_settings.company,
				"selling_price_list": shopify_settings.price_list,
				"ignore_pricing_rule": 1,
				"items": items,
				"taxes": get_order_taxes(shopify_order, shopify_settings),
				"apply_discount_on": "Grand Total",
				"discount_amount": get_discounted_amount(shopify_order),
			}
		)

		if company:
			so.update({"company": company, "status": "Draft"})
		so.flags.ignore_mandatory = True
		so.save(ignore_permissions=True)
		so.submit()

	else:
		so = frappe.get_doc("Sales Order", so)

	frappe.db.commit()
	return so


def get_order_items(order_items, shopify_settings, delivery_date):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append(
				{"title": shopify_item.get("title"), "shopify_order_id": shopify_item.get("id")}
			)
			continue

		if all_product_exists:
			item_code = get_item_code(shopify_item)
			items.append(
				{
					"item_code": item_code,
					"item_name": shopify_item.get("name"),
					"rate": shopify_item.get("price"),
					"delivery_date": delivery_date,
					"qty": shopify_item.get("quantity"),
					"stock_uom": shopify_item.get("uom") or _("Nos"),
					"warehouse": shopify_settings.warehouse,
				}
			)
		else:
			items = []

	return items


def get_order_taxes(shopify_order, shopify_settings):
	taxes = []
	for tax in shopify_order.get("tax_lines"):
		taxes.append(
			{
				"charge_type": _("On Net Total"),
				"account_head": get_tax_account_head(tax),
				"description": "{0} - {1}%".format(tax.get("title"), tax.get("rate") * 100.0),
				"rate": tax.get("rate") * 100.00,
				"included_in_print_rate": 1 if shopify_order.get("taxes_included") else 0,
				"cost_center": shopify_settings.cost_center,
			}
		)

	taxes = update_taxes_with_shipping_lines(
		taxes, shopify_order.get("shipping_lines"), shopify_settings
	)

	return taxes


def get_tax_account_head(tax):
	tax_title = tax.get("title").encode("utf-8")

	tax_account = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": "Shopify Setting", "shopify_tax": tax_title},
		"tax_account",
	)

	if not tax_account:
		frappe.throw(
			_("Tax Account not specified for Shopify Tax {0}").format(tax.get("title"))
		)

	return tax_account


def get_discounted_amount(order):
	discounted_amount = 0.0
	for discount in order.get("discount_codes"):
		discounted_amount += flt(discount.get("amount"))
	return discounted_amount


def update_taxes_with_shipping_lines(taxes, shipping_lines, shopify_settings):
	"""Shipping lines represents the shipping details,
	each such shipping detail consists of a list of tax_lines"""
	for shipping_charge in shipping_lines:
		if shipping_charge.get("price"):
			taxes.append(
				{
					"charge_type": _("Actual"),
					"account_head": get_tax_account_head(shipping_charge),
					"description": shipping_charge["title"],
					"tax_amount": shipping_charge["price"],
					"cost_center": shopify_settings.cost_center,
				}
			)

		for tax in shipping_charge.get("tax_lines"):
			taxes.append(
				{
					"charge_type": _("Actual"),
					"account_head": get_tax_account_head(tax),
					"description": tax["title"],
					"tax_amount": tax["price"],
					"cost_center": shopify_settings.cost_center,
				}
			)

	return taxes


def get_sales_order(shopify_order_id):
	sales_order = frappe.db.get_value(
		"Sales Order", filters={"shopify_order_id": shopify_order_id}
	)
	if sales_order:
		so = frappe.get_doc("Sales Order", sales_order)
		return so
