import type { SubscriberArgs, SubscriberConfig } from "@medusajs/framework"
import { forwardToErpnext } from "../lib/forward"

/**
 * Order-scoped Medusa v2 events forwarded to ERPNext. Must stay in sync with
 * EVENT_MAPPER in `ecommerce_integrations/.../medusa/constants.py`:
 *
 *   order.placed              -> sync_sales_order        (data: { id })
 *   order.completed           -> prepare_sales_invoice   (data: { id })
 *   order.fulfillment_created -> prepare_delivery_note   (data: { order_id, fulfillment_id })
 *   order.canceled            -> cancel_order            (data: { id })
 *   order.return_received     -> prepare_credit_note     (data: { order_id, return_id })
 *
 * Note the payload shapes differ: order.placed/completed/canceled carry the order id as
 * `data.id`, while order.fulfillment_created / order.return_received carry `data.order_id`
 * (+ fulfillment_id / return_id). We normalise everything to the order (or {order_id,
 * return}) object the ERPNext handlers expect before forwarding.
 */

const ORDER_FIELDS = [
  "id",
  "display_id",
  "status",
  "email",
  "currency_code",
  "region_id",
  "customer_id",
  "created_at",
  "canceled_at",
  "tax_total",
  "shipping_total",
  "discount_total",
  "item_total",
  "subtotal",
  "total",
  "customer.*",
  "items.*",
  "shipping_address.*",
  "billing_address.*",
  "shipping_methods.*",
  "payment_collections.*",
  "payment_collections.payments.*",
  "fulfillments.*",
  "fulfillments.items.*",
  "fulfillments.labels.*",
]

const RETURN_FIELDS = ["id", "order_id", "status", "refund_amount", "location_id", "items.*"]

async function fetchOne(
  container: SubscriberArgs["container"],
  entity: string,
  id: string,
  fields: string[]
): Promise<Record<string, unknown> | undefined> {
  const query = container.resolve("query")
  const { data } = await query.graph({ entity, fields, filters: { id } })
  return data?.[0]
}

export default async function erpnextSync({
  event,
  container,
}: SubscriberArgs<{ id?: string; order_id?: string; return_id?: string }>) {
  const topic = event.name
  const data = event.data ?? {}

  // The order id lives in data.id for order.* events and data.order_id for the
  // fulfillment/return events.
  const orderId = data.id ?? data.order_id

  if (!orderId) {
    console.error(`[erpnext-sync] event ${topic} had no order id; skipping`)
    return
  }

  if (topic === "order.return_received") {
    // forward { order_id, return } so the credit-note handler can match the SI.
    const returnId = data.return_id
    const returnObj = returnId ? await fetchOne(container, "return", returnId, RETURN_FIELDS) : undefined
    if (!returnObj) {
      console.error(`[erpnext-sync] could not load return id=${returnId}; skipping`)
      return
    }
    await forwardToErpnext(topic, { order_id: orderId, return: returnObj })
    return
  }

  // every other topic forwards the full order (the fulfillment handler iterates
  // order.fulfillments, deduped, so re-sending the whole order is safe).
  const order = await fetchOne(container, "order", orderId, ORDER_FIELDS)
  if (!order) {
    console.error(`[erpnext-sync] could not load order id=${orderId} for ${topic}; skipping`)
    return
  }
  await forwardToErpnext(topic, order)
}

export const config: SubscriberConfig = {
  event: [
    "order.placed",
    "order.completed",
    "order.fulfillment_created",
    "order.canceled",
    "order.return_received",
  ],
}
