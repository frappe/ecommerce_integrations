import type { SubscriberArgs, SubscriberConfig } from "@medusajs/framework"
import { Modules } from "@medusajs/framework/utils"
import { forwardToErpnext } from "../lib/forward"

/**
 * Topics forwarded to ERPNext. Must stay in sync with EVENT_MAPPER in
 * `ecommerce_integrations/ecommerce_integrations/medusa/constants.py`:
 *
 *   order.placed            -> sync_sales_order
 *   order.payment_captured  -> prepare_sales_invoice
 *   fulfillment.created     -> prepare_delivery_note
 *   order.canceled          -> cancel_order
 *   order.return_received   -> prepare_credit_note
 */

// Field sets requested via the Query API so ERPNext receives complete objects,
// matching the Medusa v2 data model the connector maps off.
// TODO: confirm the exact Medusa v2 fields/service for each event against your
// Medusa version — list-of-fields strings are version sensitive.
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

const FULFILLMENT_FIELDS = [
  "id",
  "location_id",
  "shipped_at",
  "delivered_at",
  "packed_at",
  "order.id",
  "order.display_id",
  "items.*",
  "labels.*",
]

const RETURN_FIELDS = [
  "id",
  "order_id",
  "status",
  "refund_amount",
  "location_id",
  "items.*",
]

/**
 * Fetch one full entity via Medusa v2's Query graph and return the first row.
 */
async function fetchOne(
  container: SubscriberArgs<{ id: string }>["container"],
  entity: string,
  id: string,
  fields: string[]
): Promise<Record<string, unknown> | undefined> {
  const query = container.resolve("query")
  const { data } = await query.graph({
    entity,
    fields,
    filters: { id },
  })
  return data?.[0]
}

export default async function erpnextSync({
  event,
  container,
}: SubscriberArgs<{ id: string }>) {
  const topic = event.name
  const id = event.data?.id

  if (!id) {
    console.error(`[erpnext-sync] event ${topic} had no data.id; skipping`)
    return
  }

  let fullObject: Record<string, unknown> | undefined

  switch (topic) {
    case "order.placed":
    case "order.payment_captured":
    case "order.canceled":
      // event.data.id is the order id for these topics.
      // TODO: confirm the order entity/fields for your Medusa v2 version.
      fullObject = await fetchOne(container, "order", id, ORDER_FIELDS)
      break

    case "fulfillment.created":
      // event.data.id is the fulfillment id.
      // TODO: confirm the fulfillment entity/fields for your Medusa v2 version.
      fullObject = await fetchOne(container, "fulfillment", id, FULFILLMENT_FIELDS)
      break

    case "order.return_received":
      // event.data.id is the return id.
      // TODO: confirm the return entity/fields for your Medusa v2 version.
      fullObject = await fetchOne(container, "return", id, RETURN_FIELDS)
      break

    default:
      console.error(`[erpnext-sync] unhandled topic ${topic}; skipping`)
      return
  }

  if (!fullObject) {
    console.error(`[erpnext-sync] could not load ${topic} object id=${id}; skipping`)
    return
  }

  // The ERPNext handlers read the bare object (e.g. the order dict), so we
  // forward the object itself rather than wrapping it in {order: ...}.
  await forwardToErpnext(topic, fullObject)
}

export const config: SubscriberConfig = {
  event: [
    "order.placed",
    "order.payment_captured",
    "fulfillment.created",
    "order.canceled",
    "order.return_received",
  ],
}

// Referenced only to keep the Modules import meaningful for consumers who prefer
// resolving module services directly instead of the Query graph.
export const RELEVANT_MODULES = [Modules.ORDER, Modules.FULFILLMENT]
