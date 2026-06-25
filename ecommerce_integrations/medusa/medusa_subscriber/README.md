# Medusa → ERPNext subscriber

A small **Medusa v2** subscriber that forwards a handful of order/fulfillment/return
events to the ERPNext [`ecommerce_integrations`](https://github.com/frappe/ecommerce_integrations)
Medusa connector. It signs each event the same way the connector verifies it, so
ERPNext can ingest fully-formed orders, invoices, delivery notes, cancellations and
credit notes.

This package is **not** an npm dependency — it's a pair of source files you copy
into your own Medusa app.

## Install

1. Copy the two source files into your Medusa app's `src/` (keep the layout):

   ```
   your-medusa-app/
     src/
       lib/forward.ts
       subscribers/erpnext-sync.ts
   ```

   (Medusa v2 auto-discovers anything under `src/subscribers/`.)

2. Set two environment variables on the Medusa app:

   | Env var | Value |
   | --- | --- |
   | `MEDUSA_ERPNEXT_URL` | The **callback URL** shown in the *Medusa Setting* in ERPNext, e.g. `https://erp.example.com/api/method/ecommerce_integrations.medusa.connection.store_request_data` |
   | `MEDUSA_ERPNEXT_WEBHOOK_SECRET` | Must be **exactly** the *Webhook Secret* from the same *Medusa Setting* |

   The signature won't validate if the secret differs by even one character.

3. Restart Medusa. Place a test order and watch the *Ecommerce Integration Log*
   list in ERPNext for a new row.

## Forwarded topics

These map 1:1 to `EVENT_MAPPER` in the connector's `constants.py`:

| Medusa topic | ERPNext handler |
| --- | --- |
| `order.placed` | `sync_sales_order` |
| `order.completed` | `prepare_sales_invoice` |
| `order.fulfillment_created` | `prepare_delivery_note` |
| `order.canceled` | `cancel_order` |
| `order.return_received` | `prepare_credit_note` |

## How it works

- The subscriber receives an event (`{ id }`), then uses Medusa v2's **Query graph**
  (`container.resolve("query")`) to load the *full* order / fulfillment / return so
  ERPNext gets complete data, not just an id.
- `forwardToErpnext()` JSON-stringifies that object, computes
  `base64(HMAC-SHA256(body, MEDUSA_ERPNEXT_WEBHOOK_SECRET))`, and POSTs it with:
  - `Content-Type: application/json`
  - `X-Medusa-Topic: <topic>`
  - `X-Medusa-Hmac-Sha256: <signature>`
- The body string is both signed and sent, so the HMAC matches the raw bytes the
  connector recomputes over.

## Retries / compensation (optional)

`forwardToErpnext()` throws on a non-2xx response, but a bare subscriber does not
retry. For at-least-once delivery, wrap the forward in a **Medusa workflow** and
trigger that workflow from the subscriber:

```ts
import { createWorkflow, createStep, StepResponse } from "@medusajs/framework/workflows-sdk"

const forwardStep = createStep("forward-to-erpnext", async (input, { container }) => {
  await forwardToErpnext(input.topic, input.payload)
  return new StepResponse(null)
})

export const forwardToErpnextWorkflow = createWorkflow("forward-to-erpnext", (input) => {
  forwardStep(input)
})
```

Medusa's workflow engine persists step state and can retry failed steps
(`maxRetries` / `retryInterval`) and run compensation, which is the recommended
production pattern over a plain subscriber. ERPNext-side deduplication (keyed on
`medusa_order_id` etc.) makes redelivery safe.

## Notes / TODO

- The Query `fields` lists in `erpnext-sync.ts` are version-sensitive. Confirm them
  against your Medusa v2 release (search for the `// TODO: confirm` markers).
- If you prefer resolving module services directly (e.g.
  `container.resolve(Modules.ORDER)`) instead of the Query graph, swap `fetchOne()`
  accordingly.
