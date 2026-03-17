import json
from typing import Literal, Optional

import frappe
import pytz
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate
from shopify import GraphQL

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import (
	CUSTOMER_ID_FIELD,
	EVENT_MAPPER,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SETTING_DOCTYPE,
	SHOPIFY_LINE_ITEM_ID_FIELD,
)
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.product import (
	create_items_if_not_exist,
	get_item_code,
)
from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

DEFAULT_TAX_FIELDS = {
	"sales_tax": "default_sales_tax_account",
	"shipping": "default_shipping_charges_account",
}


def sync_sales_order(payload, request_id=None):
	order = payload
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	if frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: cstr(order["id"])}):
		create_shopify_log(status="Invalid", message="Sales order already exists, not synced")
		return
	try:
		shopify_customer = order.get("customer") if order.get("customer") is not None else {}
		shopify_customer["billing_address"] = order.get("billing_address", "")
		shopify_customer["shipping_address"] = order.get("shipping_address", "")
		customer_id = shopify_customer.get("id")
		if customer_id:
			customer = ShopifyCustomer(customer_id=customer_id)
			if not customer.is_synced():
				customer.sync_customer(customer=shopify_customer)
			else:
				customer.update_existing_addresses(shopify_customer)
		create_items_if_not_exist(order)

		setting = frappe.get_doc(SETTING_DOCTYPE)

		create_order(order, setting)
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)
	else:
		create_shopify_log(status="Success")


def create_order(order, setting, company=None):
	# local import to avoid circular dependencies
	from ecommerce_integrations.shopify.fulfillment import create_delivery_note
	from ecommerce_integrations.shopify.invoice import create_sales_invoice

	so = create_sales_order(order, setting, company)
	if so:
		if order.get("financial_status") == "PAID":
			create_sales_invoice(order, setting, so)

		if order.get("fulfillments"):
			create_delivery_note(order, setting, so)


def create_sales_order(shopify_order, setting, company=None):
	customer = setting.default_customer
	if shopify_order.get("customer", {}):
		if customer_id := shopify_order.get("customer", {}).get("id"):
			customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name")

	so = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")

	if not so:
		items = get_order_items(
			shopify_order.get("line_items"),
			setting,
			getdate(shopify_order.get("created_at")),
			taxes_inclusive=shopify_order.get("taxes_included"),
		)

		if not items:
			message = (
				"Following items exists in the shopify order but relevant records were"
				" not found in the shopify Product master"
			)
			product_not_exists = []  # fix missing items
			message += "\n" + ", ".join(product_not_exists)

			create_shopify_log(status="Error", exception=message, rollback=True)

			return ""

		taxes = get_order_taxes(shopify_order, setting, items)
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"naming_series": setting.sales_order_series or "SO-Shopify-",
				ORDER_ID_FIELD: str(shopify_order.get("id")),
				ORDER_NUMBER_FIELD: shopify_order.get("name"),
				"customer": customer,
				"transaction_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"delivery_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"company": setting.company,
				"selling_price_list": get_dummy_price_list(),
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
		normalize_item_wise_tax_detail(so.taxes)
		so.save(ignore_permissions=True)
		so.submit()

		if shopify_order.get("note"):
			so.add_comment(text=f"Order Note: {shopify_order.get('note')}")

	else:
		so = frappe.get_doc("Sales Order", so)

	return so


def get_order_items(order_items, setting, delivery_date, taxes_inclusive):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append(
				{
					"title": shopify_item.get("title"),
					ORDER_ID_FIELD: shopify_item.get("id"),
				}
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
					"warehouse": setting.warehouse,
					ORDER_ITEM_DISCOUNT_FIELD: (
						_get_total_discount(shopify_item) / cint(shopify_item.get("quantity"))
					),
					SHOPIFY_LINE_ITEM_ID_FIELD: str(shopify_item.get("id")),
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
	for tax in line_item.get("tax_lines", []):
		total_taxes += flt(tax.get("price"))

	return price - (total_taxes + total_discount) / qty


def _get_total_discount(line_item) -> float:
	discount_allocations = line_item.get("discount_allocations") or []
	return sum(flt(discount.get("amount")) for discount in discount_allocations)


def consolidate_order_taxes(taxes):
	"""
	Consolidate taxes by account_head.
	Always returns a list of dicts and keeps item_wise_tax_detail as a dict here.
	The caller must stringify item_wise_tax_detail before inserting into ERPNext.
	"""
	tax_account_wise_data = {}
	for tax in taxes or []:
		account_head = tax.get("account_head")
		if not account_head:
			# skip malformed rows
			continue

		if account_head not in tax_account_wise_data:
			tax_account_wise_data[account_head] = {
				"charge_type": "Actual",
				"account_head": account_head,
				"description": tax.get("description"),
				"cost_center": tax.get("cost_center"),
				"included_in_print_rate": tax.get("included_in_print_rate", 0),
				"dont_recompute_tax": tax.get("dont_recompute_tax", 1),
				"tax_amount": 0.0,
				"item_wise_tax_detail": {},
			}

		entry = tax_account_wise_data[account_head]
		entry["tax_amount"] = flt(entry.get("tax_amount", 0.0)) + flt(tax.get("tax_amount", 0.0))

		# Merge item_wise_tax_detail if present (accept dict or JSON-string)
		item_detail = tax.get("item_wise_tax_detail") or {}
		if isinstance(item_detail, str):
			try:
				item_detail = json.loads(item_detail)
			except Exception:
				item_detail = {}
		if isinstance(item_detail, dict):
			entry["item_wise_tax_detail"].update(item_detail)

	# return as list (ERPNext expects an iterable of dicts)
	return list(tax_account_wise_data.values())


def get_order_taxes(shopify_order, setting, items):
	"""
	Build taxes list for an order.

	IMPORTANT: ERPNext expects tax.item_wise_tax_detail to be a JSON STRING when it
	reads tax rows. So we keep item_wise_tax_detail as dict while building, then
	stringify all rows at the end.
	"""
	taxes = []
	line_items = shopify_order.get("line_items") or []

	for line_item in line_items:
		item_code = get_item_code(line_item)
		for tax in line_item.get("tax_lines") or []:
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax, charge_type="sales_tax"),
					"description": (
						get_tax_account_description(tax)
						or f"{tax.get('title')} - {flt(tax.get('rate')) * 100.0:.2f}%"
					),
					"tax_amount": flt(tax.get("price")),
					"included_in_print_rate": 0,
					"cost_center": setting.cost_center,
					"item_wise_tax_detail": {
						item_code: {
							"rate": flt(tax.get("rate")) * 100,
							"tax_amount": flt(tax.get("price")),
						}
					},
					"dont_recompute_tax": 1,
				}
			)

	# Update taxes with shipping lines (this function should append dicts similarly)
	update_taxes_with_shipping_lines(
		taxes,
		shopify_order.get("shipping_lines") or [],
		setting,
		items,
		taxes_inclusive=bool(shopify_order.get("taxes_included")),
	)

	# Consolidate if requested (returns list)
	if cint(setting.consolidate_taxes):
		taxes = consolidate_order_taxes(taxes)

	# ensure every row has item_wise_tax_detail as JSON string ---
	normalized = []
	for row in taxes or []:
		r = dict(row)

		detail = r.get("item_wise_tax_detail")

		if isinstance(detail, dict):
			r["item_wise_tax_detail"] = json.dumps(detail)
		elif isinstance(detail, list):
			converted = {}
			for idx, entry in enumerate(detail):
				if isinstance(entry, dict):
					key = entry.get("item_code") or entry.get("name") or f"row_{idx+1}"
					converted[key] = entry
				else:
					converted[f"row_{idx+1}"] = {"tax": entry}
			r["item_wise_tax_detail"] = json.dumps(converted)
		elif isinstance(detail, str):
			try:
				loaded = json.loads(detail)
				if isinstance(loaded, dict):
					r["item_wise_tax_detail"] = json.dumps(loaded)
				else:
					r["item_wise_tax_detail"] = "{}"
			except Exception:
				r["item_wise_tax_detail"] = "{}"
		else:
			r["item_wise_tax_detail"] = "{}"

		normalized.append(r)

	return normalized


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
						"delivery_date": (items[-1]["delivery_date"] if items else nowdate()),
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
					"item_wise_tax_detail": (
						{
							setting.shipping_item: {
								"rate": flt(tax.get("rate")) * 100,
								"tax_amount": flt(tax.get("price")),
							}
						}
						if shipping_as_item
						else {}
					),
					"dont_recompute_tax": 1,
				}
			)


def normalize_item_wise_tax_detail(taxes):
	import json

	for row in taxes:
		val = row.get("item_wise_tax_detail")

		if isinstance(val, dict):
			row.set("item_wise_tax_detail", json.dumps(val))
			continue

		if isinstance(val, list):
			row.set("item_wise_tax_detail", "{}")
			continue

		if val is None:
			row.set("item_wise_tax_detail", "{}")
			continue

		if isinstance(val, str):
			try:
				loaded = json.loads(val)
				if isinstance(loaded, dict):
					row.set("item_wise_tax_detail", json.dumps(loaded))
				else:
					row.set("item_wise_tax_detail", "{}")
			except Exception:
				row.set("item_wise_tax_detail", "{}")
			continue

		row.set("item_wise_tax_detail", "{}")


def get_sales_order(order_id):
	"""Get ERPNext sales order using shopify order id."""
	sales_order = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: order_id})
	if sales_order:
		return frappe.get_doc("Sales Order", sales_order)


def cancel_order(payload, request_id=None):
	"""Called by order/cancelled event.

	When shopify order is cancelled there could be many different someone handles it.

	Updates document with custom field showing order status.

	IF sales invoice / delivery notes are not generated against an order, then cancel it.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	order = payload

	try:
		order_id = order["id"]
		order_status = order["financial_status"]

		sales_order = get_sales_order(order_id)

		if not sales_order:
			create_shopify_log(status="Invalid", message="Sales Order does not exist")
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
		create_shopify_log(status="Error", exception=e)
	else:
		create_shopify_log(status="Success")


@temp_shopify_session
def sync_old_orders():
	shopify_setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not cint(shopify_setting.sync_old_orders):
		return

	orders = _fetch_old_orders(shopify_setting.old_orders_from, shopify_setting.old_orders_to)

	for order in orders:
		log = create_shopify_log(
			method=EVENT_MAPPER["orders/create"],
			request_data=json.dumps(order),
			make_new=True,
		)
		sync_sales_order(order, request_id=log.name)

	shopify_setting = frappe.get_doc(SETTING_DOCTYPE)
	shopify_setting.sync_old_orders = 0


def _fetch_old_orders(from_time, to_time, limit=50):
	frappe.set_user("Administrator")
	frappe.logger().info("Fetching old orders from Shopify...")

	from_time = get_datetime(from_time).astimezone(pytz.UTC).isoformat()
	to_time = get_datetime(to_time).astimezone(pytz.UTC).isoformat()

	query = """
    query GetOrdersByDateRange($query: String!, $limit: Int!, $cursor: String) {
        orders(first: $limit, query: $query, after: $cursor) {
            edges {
                cursor
                node {
                    id
                    name
                    createdAt
                    updatedAt
                    processedAt
                    cancelledAt
                    closedAt
                    confirmed
                    test
                    displayFinancialStatus
                    displayFulfillmentStatus
                    taxesIncluded
                    currencyCode
                    note
                    tags
                    cancelReason
                    totalWeight
                    totalPriceSet {
                        presentmentMoney {
                            amount
                            currencyCode
                        }
                    }
                    totalDiscountsSet {
                        presentmentMoney {
                            amount
                            currencyCode
                        }
                    }
                    currentSubtotalPriceSet {
                        presentmentMoney {
                            amount
                            currencyCode
                        }
                    }
                    totalOutstandingSet {
                        presentmentMoney {
                            amount
                            currencyCode
                        }
                    }
                    totalShippingPriceSet {
                        presentmentMoney {
                            amount
                            currencyCode
                        }
                    }
                    totalTipReceived {
                        amount
                        currencyCode
                    }
                    customer {
                        id
                        email
                        firstName
                        lastName
                        phone
                        state
                        taxExempt
                        taxExemptions
                        emailMarketingConsent {
                            consentUpdatedAt
                            marketingOptInLevel
                            marketingState
                        }
                        smsMarketingConsent {
                            consentCollectedFrom
                            consentUpdatedAt
                            marketingOptInLevel
                            marketingState
                        }
                        defaultAddress {
                            id
                            firstName
                            lastName
                            company
                            address1
                            address2
                            city
                            province
                            country
                            zip
                            phone
                            provinceCode
                            countryCodeV2
                        }
                    }
                    billingAddress {
                        firstName
                        lastName
                        company
                        address1
                        address2
                        city
                        province
                        country
                        zip
                        phone
                        name
                        provinceCode
                        countryCodeV2
                    }
                    shippingAddress {
                        firstName
                        lastName
                        company
                        address1
                        address2
                        city
                        province
                        country
                        zip
                        phone
                        name
                        provinceCode
                        countryCodeV2
                    }
                    shippingLines(first: 10) {
                        edges {
                            node {
                                id
                                title
                                code
                                carrierIdentifier
                                discountedPriceSet {
                                    presentmentMoney {
                                        amount
                                        currencyCode
                                    }
                                }
                            }
                        }
                    }
                    lineItems(first: 50) {
                        edges {
                            node {
                                id
                                name
                                quantity
                                sku
                                vendor
                                taxable
                                currentQuantity
                                fulfillableQuantity
                                variant {
                                    id
                                    title
                                    inventoryItem {
                                        id
                                        tracked
                                    }
                                }
                                product {
                                    id
                                }
                                discountAllocations {
                                    allocatedAmountSet {
                                        presentmentMoney {
                                            amount
                                            currencyCode
                                        }
                                    }
                                }
                                totalDiscountSet {
                                    presentmentMoney {
                                        amount
                                        currencyCode
                                    }
                                }
                                fulfillmentService {
                                    serviceName
                                    type
                                }
                            }
                        }
                    }
                    fulfillments(first: 10) {
                        id
                        status
                        createdAt
                        updatedAt
                        trackingInfo {
                            company
                            number
                            url
                        }
                        fulfillmentLineItems(first: 50) {
                            edges {
                                node {
                                    id
                                    quantity
                                    lineItem {
                                        id
                                        name
                                    }
                                }
                            }
                        }
                    }
                }
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """

	search_query = f'createdAt:>="{from_time}" AND createdAt:<="{to_time}"'

	cursor = None
	has_next_page = True
	total_orders = 0

	while has_next_page:
		frappe.logger().info(f"Querying Shopify with cursor: {cursor}")
		variables = {"query": search_query, "limit": limit, "cursor": cursor}
		response = json.loads(GraphQL().execute(query, variables))

		if not response:
			frappe.logger().error("Empty response from Shopify GraphQL API.")
			break

		if "errors" in response:
			frappe.log_error(json.dumps(response["errors"], indent=2), "Shopify Order Fetch Error")
			break

		orders_data = response.get("data", {}).get("orders", {})
		for edge in orders_data.get("edges", []):
			node = edge["node"]
			customer = node.get("customer") or {}
			customer_id = customer.get("id")
			if customer_id:
				customer_id = int(customer_id.split("/")[-1])
			else:
				customer_id = None

			def money_amount(obj):
				return obj.get("presentmentMoney", {}).get("amount", "0.0") if obj else "0.0"

			normalized_order = {
				"id": int(node["id"].split("/")[-1]),
				"admin_graphql_api_id": node["id"],
				"name": node.get("name"),
				"order_number": int(node.get("name", "#0").replace("#", "")),
				"email": customer.get("email"),
				"phone": customer.get("phone"),
				"currency": node.get("currencyCode"),
				"financial_status": node.get("displayFinancialStatus"),
				"fulfillment_status": node.get("displayFulfillmentStatus"),
				"total_price": money_amount(node.get("totalPriceSet")),
				"subtotal_price": money_amount(node.get("currentSubtotalPriceSet")),
				"total_discounts": money_amount(node.get("totalDiscountsSet")),
				"total_tax": money_amount(node.get("totalTaxSet")),
				"total_weight": node.get("totalWeight"),
				"taxes_included": node.get("taxesIncluded"),
				"confirmed": node.get("confirmed"),
				"test": node.get("test"),
				"created_at": node.get("createdAt"),
				"updated_at": node.get("updatedAt"),
				"processed_at": node.get("processedAt"),
				"cancelled_at": node.get("cancelledAt"),
				"closed_at": node.get("closedAt"),
				"source_name": node.get("sourceName", None),
				"tags": node.get("tags", []),
				"note": node.get("note"),
				"billing_address": node.get("billingAddress", {}),
				"shipping_address": node.get("shippingAddress", {}),
				"customer": {
					"id": customer_id,
					"first_name": customer.get("firstName"),
					"last_name": customer.get("lastName"),
					"email": customer.get("email"),
					"phone": customer.get("phone"),
					"tags": customer.get("tags", ""),
					"tax_exempt": customer.get("taxExempt", False),
					"currency": node.get("currencyCode"),
					"default_address": customer.get("defaultAddress", {}),
				},
				"line_items": [],
				"fulfillments": [],
				"shipping_lines": [],
				"discount_applications": [],
				"payment_terms": None,
				"refunds": [],
			}
			line_items = (node or {}).get("lineItems", {}) or {}
			edges = line_items.get("edges", []) or []

			# Normalize line items (CRASH-PROOF VERSION)
			for li_edge in edges:
				# skip empty or invalid edges
				if not li_edge or not isinstance(li_edge, dict):
					continue

				li = li_edge.get("node")
				if not li or not isinstance(li, dict):
					continue

				# product and variant can be null from Shopify
				product_obj = li.get("product") or {}
				variant_obj = li.get("variant") or {}
				fulfillment_obj = li.get("fulfillmentService") or {}

				# Safe product_id + variant_id extraction
				product_gid = product_obj.get("id")
				product_id = int(product_gid.split("/")[-1]) if product_gid else None

				variant_gid = variant_obj.get("id")
				variant_id = int(variant_gid.split("/")[-1]) if variant_gid else None

				normalized_order["line_items"].append(
					{
						"id": (int(li.get("id", "").split("/")[-1]) if li.get("id") else None),
						"admin_graphql_api_id": li.get("id"),
						"name": li.get("name"),
						"quantity": li.get("quantity"),
						"sku": li.get("sku"),
						"vendor": li.get("vendor"),
						"taxable": li.get("taxable"),
						"current_quantity": li.get("currentQuantity"),
						"fulfillable_quantity": li.get("fulfillableQuantity"),
						# Safe fulfillment service
						"fulfillment_service": fulfillment_obj.get("serviceName"),
						# SAFE fields
						"product_exists": bool(product_id),
						"variant_id": variant_id,
						"variant_title": variant_obj.get("title"),
						"product_id": product_id,
						# discount allocations
						"discount_allocations": li.get("discountAllocations", []),
						"total_discount": money_amount(li.get("totalDiscountSet")),
					}
				)

			# Normalize line items

			total_orders += 1
			yield normalized_order

		page_info = orders_data.get("pageInfo", {})
		has_next_page = page_info.get("hasNextPage")
		cursor = page_info.get("endCursor")

	frappe.logger().info(f"Finished fetching {total_orders} orders.")
