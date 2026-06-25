# Medusa Integration

Two-way integration between [Medusa](https://medusajs.com/) (v2) and ERPNext.

Medusa is a self-hosted, open-source commerce engine. Unlike the hosted platforms
in this app (Shopify, Amazon), there is **no central API to register webhooks** —
events are delivered by a small server-side *subscriber* you install in your Medusa
project, which HMAC-signs each event and POSTs it to ERPNext. Everything else
(reading orders/products, pushing inventory, creating products) goes through
Medusa's **Admin API** using a single static Admin API key.

## Overview

| Direction | Flow | Trigger |
| --- | --- | --- |
| Medusa → ERPNext | Order → **Sales Order** | `order.placed` webhook |
| Medusa → ERPNext | Payment captured → **Sales Invoice** + Payment Entry | `order.completed` webhook |
| Medusa → ERPNext | Fulfillment → **Delivery Note** | `order.fulfillment_created` webhook |
| Medusa → ERPNext | Order canceled → cancel/flag SO/SI/DN | `order.canceled` webhook |
| Medusa → ERPNext | Return received → **Credit Note** (return Sales Invoice) | `order.return_received` webhook |
| Medusa → ERPNext | Product → **Item** (+ variants) | lazily, when first ordered |
| ERPNext → Medusa | Item → Medusa **Product**/variant | `Item` save hook |
| ERPNext → Medusa | Stock levels → Medusa inventory | scheduler |
| Medusa → ERPNext | Historical **order backfill** | manual toggle in Setting |

## Prerequisites

- ERPNext with the `ecommerce_integrations` app installed.
- A running Medusa **v2** server you can reach from your ERPNext site.
- A Medusa **Admin API key** (see below).
- The companion **Medusa subscriber** installed in your Medusa project (see below).
- A Company, a default Customer, a Customer Group, and at least one Warehouse in
  ERPNext.

## Getting a Medusa Admin API key

The connector authenticates to the Admin API with a *secret* API key over HTTP Basic
auth (the key as username, empty password — handled for you in `connection.py`).

1. Log in to your Medusa Admin dashboard.
2. Go to **Settings → Secret API keys**.
3. Create a key, give it a descriptive name (e.g. `erpnext`), and copy the secret.
   It is shown only once.
4. Paste it into **Medusa Setting → Admin API Key** in ERPNext.

> Use a *secret* key, not a *publishable* key. Publishable keys cannot read the
> Admin API.

## Installing the companion subscriber + webhook URL/secret

Medusa v2 has no admin endpoint to register webhooks, so this app ships a subscriber
that you drop into your Medusa project's `src/subscribers/`. It:

- listens for the five events in `constants.WEBHOOK_EVENTS`
  (`order.placed`, `order.completed`, `order.fulfillment_created`,
  `order.canceled`, `order.return_received`),
- HMAC-SHA256 signs the JSON body with a shared **webhook secret**, putting the
  base64 digest in the `X-Medusa-Hmac-Sha256` header and the event name in
  `X-Medusa-Topic`, and
- POSTs to ERPNext.

Setup:

1. In ERPNext, open **Medusa Setting** and tick **Enable Medusa**. The *Webhooks*
   table populates with the topics the subscriber must emit (read-only checklist —
   ERPNext cannot confirm registration on Medusa's side).
2. Copy the **callback URL** ERPNext exposes:
   `https://<your-erpnext-site>/api/method/ecommerce_integrations.medusa.connection.store_request_data`
3. Generate a strong **webhook secret** and put the *same* value in:
   - **Medusa Setting → Webhook Secret**, and
   - the subscriber config in your Medusa project.
4. Deploy/restart Medusa. Incoming events with a bad/missing signature are rejected
   and logged in **Ecommerce Integration Log**.

## The `Medusa Setting` fields

### Connection
- **Enable Medusa** — master on/off switch.
- **Medusa URL** — base URL of your Medusa server (no trailing slash; `/admin` is
  appended automatically).
- **Admin API Key** — secret Admin API key (see above).
- **Webhook Secret** — shared HMAC secret for inbound webhooks.
- **Webhooks** — read-only checklist of topics the subscriber must emit.

### Customer
- **Default Customer** — used when an order has no Medusa customer.
- **Customer Group** — group assigned to synced customers.

### Company-dependent
- **Company** — the ERPNext company orders post against.
- **Cash/Bank Account** — account for the auto Payment Entry on captured payments.
- **Cost Center** — cost center stamped on invoice/charge rows.

### Orders
- **Sales Order Series / Delivery Note Series / Sales Invoice Series** — naming
  series for the generated documents.
- **Shipping Item** — item used when *Add Shipping as Item* is enabled.
- **Sync Delivery Note** — create a Delivery Note per fulfillment.
- **Sync Sales Invoice** — create a Sales Invoice (+ Payment Entry) on capture.
- **Sync Returns** — create a Credit Note on `order.return_received`.
- **Add Shipping as Item** — bill shipping as a line item instead of a tax/charge row.
- **Consolidate Taxes** — merge per-line tax rows that hit the same account head.

### Pricing
- **Use Price List** — price orders against a real price list instead of the dummy
  one.
- **Selling Price List** — the price list used when the above is on.

### Taxes
- **Taxes** child table — map a Medusa tax title → ERPNext **Tax Account** +
  **Tax Description**.
- **Default Sales Tax Account** — fallback account for line taxes.
- **Default Shipping Charges Account** — fallback account for shipping.

### ERPNext → Medusa product sync
- **Upload ERPNext Items** — push new ERPNext Items to Medusa on save.
- **Update Medusa Item on Update** — push subsequent edits.
- **Sync New Item as Active** — create Medusa products as `published` (else `draft`).
- **Upload Variants as Items** — also push variant Items.

### Inventory sync
- **Update ERPNext Stock Levels to Medusa** — enable the scheduled inventory push.
- **Warehouse** — default ERPNext warehouse for synced documents.
- **Inventory Sync Frequency** — minutes between pushes (5/10/15/30/60).
- **Warehouse Mapping** child table — map a Medusa **stock location** to an ERPNext
  **Warehouse**. Use *Fetch Medusa Locations* to populate the location ids.

### Order backfill
- **Sync Old Orders** — one-shot toggle to import historical orders. Resets itself
  after the run.
- **Old Orders From / To** — the date window to backfill.

## Sync flows in detail

### Products (both ways)
- **Medusa → ERPNext**: products are created lazily. When an order references a
  product/variant that is not yet linked via an **Ecommerce Item**, `product.py`
  fetches it from the Admin API and creates a single Item (one variant) or an Item
  template + variant Items (options/multiple variants). Medusa variant `sku` becomes
  the Item code; weight (grams) maps to the `Gram` UOM.
- **ERPNext → Medusa**: the `Item` save hook (`upload_erpnext_item`) creates a Medusa
  product + variant for a new Item, or updates an existing one, gated on the upload
  toggles.

### Orders → Sales Order (`order.placed`)
`order.sync_sales_order` dedupes on `medusa_order_id`, lazily syncs the line-item
products and the customer (with billing/shipping addresses from the order snapshot),
then builds and submits a **Sales Order**. Line items carry unit price, quantity, and
a per-unit discount (`medusa_item_discount`); per-line taxes and the shipping charge
become **Sales Taxes and Charges** rows (consolidated if configured).

### Invoice + payment (`order.completed`)
`invoice.prepare_sales_invoice` finds the submitted Sales Order, makes and submits a
**Sales Invoice**, and — if the grand total is positive — creates and submits a
**Payment Entry** against the configured cash/bank account. Deduped on the invoice's
`medusa_order_id`.

### Fulfillment → Delivery Note (`order.fulfillment_created`)
`fulfillment.prepare_delivery_note` creates one **Delivery Note** per Medusa
fulfillment (deduped on `medusa_fulfillment_id`), shipping the fulfilled quantities
from the warehouse mapped to the fulfillment's stock location.

### Returns → Credit Note (`order.return_received`)
`returns.prepare_credit_note` builds a return **Sales Invoice** (credit note) from the
original invoice. A full return mirrors the invoice with negated quantities and taxes;
a partial return keeps only the returned lines and **prorates each tax row** by the
returned quantity (same approach as the Unicommerce connector). Deduped on
`medusa_return_id`.

### Inventory: ERPNext → Medusa
`inventory.update_inventory_on_medusa` runs on the scheduler. It computes the delta of
changed **Bin** levels per mapped warehouse, resolves each variant SKU to a Medusa
`inventory_item_id` (`GET /admin/inventory-items?sku=…`), and sets the stocked
quantity at the mapped location (`actual_qty − reserved_qty`). Each synced
**Ecommerce Item** advances its `inventory_synced_on` watermark; pushes are batched
with a per-batch log.

### Order backfill
`order.sync_old_orders` (manual toggle) pages historical orders from the Admin API in
the configured date window and runs each through the normal `sync_sales_order` path,
then clears the toggle.

## Logs & troubleshooting

Every inbound event and every push creates an **Ecommerce Integration Log**
(`integration = medusa`). Failed signature validation, unhandled topics, and sync
errors are all recorded there with the offending payload. Start there when an order or
inventory push does not appear.

## Feature parity

| Feature | Medusa | Shopify | Amazon | Unicommerce | Zenoti |
| --- | :---: | :---: | :---: | :---: | :---: |
| Order → Sales Order | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sales Invoice + payment | ✅ | ✅ | ➖ | ✅ | ✅ |
| Fulfillment → Delivery Note | ✅ | ✅ | ➖ | ✅ | ➖ |
| Returns → Credit Note | ✅ | ➖ | ➖ | ✅ | ➖ |
| Order cancellation | ✅ | ✅ | ➖ | ✅ | ➖ |
| Product Medusa → ERPNext | ✅ | ✅ | ✅ | ➖ | ➖ |
| Product ERPNext → Medusa | ✅ | ✅ | ➖ | ✅ | ➖ |
| Variants | ✅ | ✅ | ➖ | ✅ | ➖ |
| Inventory ERPNext → platform | ✅ | ✅ | ➖ | ✅ | ➖ |
| Customer sync | ✅ | ✅ | ➖ | ✅ | ✅ |
| Historical order backfill | ✅ | ✅ | ✅ | ➖ | ➖ |
| Webhook registration via API | ➖¹ | ✅ | n/a | n/a | n/a |

¹ Medusa v2 has no admin webhook-registration API; events are delivered by the
companion subscriber you install in your Medusa project.

Legend: ✅ supported · ➖ not applicable / not implemented.
