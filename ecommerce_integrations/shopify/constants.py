# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt


MODULE_NAME = "Shopify"
SETTING_DOCTYPE = "Shopify Setting"

SHOPIFY_CUSTOMER_FIELD = "shopify_customer_id"

API_VERSION = "2021-04"

WEBHOOK_EVENTS = [
	"orders/create",
	"orders/paid",
	"orders/fulfilled",
]

EVENT_MAPPER = {
	"orders/create": "ecommerce_integrations.shopify.order.sync_sales_order",
	"orders/paid" : "ecommerce_integrations.shopify.doctype.orders.create_sales_invoice",
	"orders/fulfilled": "ecommerce_integrations.shopify.doctype.orders.prepare_delivery_note"
}
