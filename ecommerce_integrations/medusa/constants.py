# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE


MODULE_NAME = "medusa"
SETTING_DOCTYPE = "Medusa Setting"

# Medusa is self-hosted; its Admin API is versioned by the running Medusa release
# rather than a dated API version (unlike Shopify). Kept for parity / future use.
API_VERSION = "v2"

# Topics emitted by the companion Medusa subscriber (see ``medusa_subscriber/``).
#
# Unlike Shopify, Medusa v2 exposes **no admin API to register webhooks** — events
# are delivered by a server-side subscriber installed in the merchant's Medusa app
# which HMAC-signs the payload and POSTs it to ``connection.store_request_data``.
WEBHOOK_EVENTS = [
	"order.placed",
	"order.completed",
	"order.fulfillment_created",
	"order.canceled",
	"order.return_received",
]

# Medusa v2 order-scoped events. Note the payload shapes differ: order.placed /
# order.completed / order.canceled carry {id: <order id>}, while order.fulfillment_created
# carries {order_id, fulfillment_id} and order.return_received {order_id, return_id}. The
# companion subscriber normalises these to the order/return object before forwarding.
EVENT_MAPPER = {
	"order.placed": "ecommerce_integrations.medusa.order.sync_sales_order",
	"order.completed": "ecommerce_integrations.medusa.invoice.prepare_sales_invoice",
	"order.fulfillment_created": "ecommerce_integrations.medusa.fulfillment.prepare_delivery_note",
	"order.canceled": "ecommerce_integrations.medusa.order.cancel_order",
	"order.return_received": "ecommerce_integrations.medusa.returns.prepare_credit_note",
}

# custom fields

CUSTOMER_ID_FIELD = "medusa_customer_id"
ADDRESS_ID_FIELD = "medusa_address_id"
ORDER_ID_FIELD = "medusa_order_id"
ORDER_NUMBER_FIELD = "medusa_display_id"
ORDER_STATUS_FIELD = "medusa_order_status"
FULFILLMENT_ID_FIELD = "medusa_fulfillment_id"
RETURN_ID_FIELD = "medusa_return_id"
ORDER_ITEM_DISCOUNT_FIELD = "medusa_item_discount"
ITEM_SELLING_RATE_FIELD = "medusa_selling_rate"
# stamped on Sales Order Item and carried to SI/DN Item rows so fulfillments and
# returns can match Medusa line items (line_item_id / item_id) back to ERPNext rows.
ORDER_LINE_ID_FIELD = "medusa_order_line_id"

# Medusa stores variant weight in grams; ERPNext's matching UOM is "Gram".
WEIGHT_UOM = "Gram"
