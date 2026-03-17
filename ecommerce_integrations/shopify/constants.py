# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE


MODULE_NAME = "shopify"
SETTING_DOCTYPE = "Shopify Setting"
OLD_SETTINGS_DOCTYPE = "Shopify Settings"

API_VERSION = "2025-04"

WEBHOOK_EVENTS = [
	"ORDERS_CANCELLED",
	"ORDERS_CREATE",
	"ORDERS_FULFILLED",
	"ORDERS_PAID",
	"ORDERS_PARTIALLY_FULFILLED",
	"PRODUCTS_CREATE",
	"RETURNS_APPROVE",
	"REFUNDS_CREATE",
]

EVENT_MAPPER = {
	"orders/create": "ecommerce_integrations.shopify.order.sync_sales_order",
	"orders/paid": "ecommerce_integrations.shopify.invoice.prepare_sales_invoice",
	"orders/fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
	"orders/cancelled": "ecommerce_integrations.shopify.order.cancel_order",
	"orders/partially_fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
	"products/create": "ecommerce_integrations.shopify.product.create_item",
	"returns/approve": "ecommerce_integrations.shopify.return.process_shopify_return",
	"refunds/create": "ecommerce_integrations.shopify.return.process_invoice_return",
}

# custom fields

CUSTOMER_ID_FIELD = "shopify_customer_id"
ORDER_ID_FIELD = "shopify_order_id"
ORDER_NUMBER_FIELD = "shopify_order_number"
ORDER_STATUS_FIELD = "shopify_order_status"
FULLFILLMENT_ID_FIELD = "shopify_fulfillment_id"
SUPPLIER_ID_FIELD = "shopify_supplier_id"
ADDRESS_ID_FIELD = "shopify_address_id"
ORDER_ITEM_DISCOUNT_FIELD = "shopify_item_discount"
ITEM_SELLING_RATE_FIELD = "shopify_selling_rate"
SHOPIFY_LINE_ITEM_ID_FIELD = "shopify_line_item_id"
SHOPIFY_RETURN_ID_FIELD = "shopify_return_id"


# ERPNext already defines the default UOMs from Shopify but names are different
WEIGHT_TO_ERPNEXT_UOM_MAP = {
	"KILOGRAMS": "Kg",
	"GRAMS": "Gram",
	"POUNDS": "Lb",
	"OUNCES": "Oz",
}
