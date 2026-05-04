# Session notes — 2026-05-04

End-to-end shake-out of the Shopify integration on `nexus.local` against a real Shopify store
(`spqutq-ws.myshopify.com`, EGP). Captures every change made, every bug found, and the state
to expect when picking this up again.

> ⚠️ This file is a session log, not a spec. The authoritative onboarding doc is `ONBOARDING.md`.
> When something here contradicts that doc, treat ONBOARDING.md as canonical.

---

## TL;DR

Brought the OAuth multi-tenant Shopify integration from "saved record, nothing flowing" to
"products / customers / orders / fulfillments / cancellations all syncing end-to-end." Found
and fixed three real bugs along the way. Stood up a fresh EGP company so future demo data and
screenshots use a currency that matches the Shopify store.

---

## Bugs fixed in code (committable)

| File | Line(s) | Change | Why |
|---|---|---|---|
| `shopify/product.py` | 224 | `item_group["custom_company"] = self.company` → `item_group.custom_company = self.company` | `item_group` is a `Document`, not a dict; `[]=` raises `TypeError`. Broke any product import whose `product_type` didn't yet exist as an Item Group. |
| `shopify/order.py` | 121 (new line) | Add `"currency": shopify_order.get("currency"),` to the SO dict | Without an explicit currency, ERPNext falls back to `frappe.defaults.get_default("currency")` (e.g. SAR) which mismatches the company's Receivable account currency (e.g. EGP) → "Party Account currency and document currency should be same". The Shopify order JSON has the right currency; just pass it through. |
| `shopify/doctype/shopify_account/shopify_account.json` | `taxes` field | Remove `mandatory_depends_on: eval:doc.enable_shopify` | The tax-mapping table is genuinely optional — the integration falls back to `default_sales_tax_account` and `default_shipping_charges_account` for unmapped titles. Marking it mandatory created an impossible UI state for stores that don't need per-title routing (empty-row → row-fields-required → fill-or-delete-row → table-empty-required). |

These three diffs are the only code changes and they are all surgical fixes to bugs we
hit. They are intentionally **not** committed yet — review them on the branch first.

### Bugs we found and chose **not** to fix yet

- **`shopify/utils.py:get_user_shopify_account`** — falls back to `None` when the user has no
  `User Permission → Company`, which silently breaks `get_product_count` and any session-resolved
  call for users like `Administrator`. We tried adding a "single-tenant fallback" and reverted it
  because it's a cross-tenant data-leak hazard (single-tenant today, multi-tenant tomorrow,
  silent behavior change). Right fix is to require operator users to have `User Permission →
  Company` set; a deployment configuration, not a code change.

- **Webhook duplication on save** — every save of a Shopify Account record re-runs
  `_handle_webhooks`, which can leave stale subscriptions on Shopify if the public URL has
  changed since the last save. We worked around it by manually deleting the stale ones via the
  Webhook API. A proper fix would compare existing subscriptions to the desired set and only
  diff.

---

## Bench-level (non-code) fixes

These are configuration changes on `nexus.local`. They aren't part of the integration; just
notes so the next operator doesn't re-debug the same things.

| Fix | What we did | Why |
|---|---|---|
| Stale Property Setter on `Supplier.naming_series` | Deleted `Supplier-naming_series-fetch_from` from `tabProperty Setter` | It pointed at `supplier_group.naming_series_for_supplier_group` — a column that doesn't exist on `tabSupplier Group`. Blocked Supplier insert (and therefore product import). |
| Global default warehouse pointing at wrong company | `tabDefaultValue.parent='__default'.default_warehouse` was `Stores - B` (BrainWise) when `__default.company=BrainWise (Demo)` | Caused Item validation to fail with "Warehouse Stores - B doesn't belong to Company BrainWise (Demo)" any time `Item.update_defaults_from_item_group` fell through to global defaults. We set `default_warehouse` to `Stores - BD` to match. |
| Custom fields not installed | Ran `setup_custom_fields()` directly | The function runs on `before_save` of the Shopify Account, but every save we tried was failing earlier in validation, so the call never reached it. 19 `shopify_*` custom fields across 8 doctypes now exist. |
| `User Permission` for operator user | Added `ahmed.osama@brainwise.me → Company → BrainWise Egypt` (apply_to_all_doctypes=1) | Enables `get_user_shopify_account` to resolve this user's session to the right Shopify Account. |

---

## Demo data added

A second ERPNext company was created so the Shopify (EGP) store has a matching base currency.

### `BrainWise Egypt` (company)

| Field | Value |
|---|---|
| `default_currency` | EGP |
| `country` | Egypt |
| `abbr` | BE |
| Chart of Accounts | Standard Template (82 accounts) |
| Default warehouse | `Stores - BE` |
| Cost center | `Main - BE` |
| Cash account | `Cash - BE` |
| Default Customer (created) | `Shopify Walk-In (BE)` (currency=EGP) |
| Shipping Charges account (created) | `Shipping Charges - BE` (root_type=Income) |
| Naming series added | `SO-EG-.YYYY.-`, `SINV-EG-.YYYY.-`, `DN-EG-.YYYY.-` |
| Currency Exchange rates seeded | EGP↔SAR, EGP↔USD |

The Shopify Account `spqutq-ws.myshopify.com` is now pointed at this company.

### Shopify-side test data

5 products in the live Shopify store:

| SKU | Title | Price (EGP) | Stock |
|---|---|---|---|
| `TEST-001` | Test Product | 100 | 10 |
| `COFFEE-ETH-250` | Ethiopia Yirgacheffe 250g | 750 | 40 |
| `COFFEE-COL-250` | Colombia Huila 250g | 680 | 50 |
| `COFFEE-ESP-1KG` | Espresso Blend 1kg | 1850 | 25 |
| `BREW-V60-01` | Ceramic V60 Dripper | 1100 | 15 |

3 customers:

| Customer | Email | Role in demo |
|---|---|---|
| Test Buyer | testbuyer@example.com | Original test order (SAR — pre-Egypt-company artifact) |
| Mahmoud Hassan | mahmoud.hassan@example.com | Cairo customer; placed the fulfilled order |
| Sara Ahmed | sara.ahmed@example.com | Alexandria customer; placed the unfulfilled order and the cancelled one |

Multiple Shopify orders were placed across two batches; the second batch (after the
`currency` fix) flowed cleanly into ERPNext as SO + SI (Paid). One was fulfilled (DN created),
one was cancelled, one is paid-but-unfulfilled.

### ERPNext-side artifacts

After the dust settled, on `BrainWise Egypt`:

- 5 Sales Orders (`SAL-ORD-2026-00xx`) in EGP
- 5 Sales Invoices (`ACC-SINV-2026-00xx`), all status=Paid (the integration creates the SI
  with payment when Shopify reports `financial_status=paid`)
- 1 Delivery Note (Mahmoud Hassan's fulfilled order)
- 4 ERPNext Items (synced from the Shopify products)
- 2 Customers with `shopify_customer_id`

The earlier (`BrainWise (Demo)`, SAR) test orders from before we re-pointed are still in the
DB as historical artifacts. They aren't tied to the Egypt store and shouldn't be screenshot
fodder going forward.

---

## Tunnel / webhooks

`nexus.local` isn't publicly reachable, so we ran an `ngrok` tunnel for Shopify webhooks:

- **Tunnel host** (free ngrok, transient): `036c-197-57-119-177.ngrok-free.app`
- **Site config additions**: `host_name`, `localtunnel_url` in `sites/nexus.local/site_config.json`
- **Webhooks registered**: `orders/create`, `orders/paid`, `orders/fulfilled`, `orders/cancelled`,
  `orders/partially_fulfilled` — all pointing at the tunnel host.

> ⚠️ ngrok-free URLs are **ephemeral**. When the tunnel restarts, the host changes and registered
> webhooks become stale. Either: (a) upgrade ngrok to a reserved domain, (b) switch to Cloudflare
> Tunnel for a stable URL, or (c) accept that you'll re-register webhooks each session.

We had to clean up 5 stale webhooks pointing at a previous, dead tunnel host
(`remington-unlettered-inaptly.ngrok-free.dev`) — Shopify keeps subscriptions even after
repeated delivery failures, and they confuse routing behavior when duplicates exist.

---

## Shopify app scopes

The app is OAuth (Client Credentials grant). When we set up, scopes already included
`read/write_fulfillments` and the `read_*_fulfillment_orders` pair, but **`write_assigned_fulfillment_orders`**
and **`write_merchant_managed_fulfillment_orders`** were missing — required for fulfilling orders
via API. Added via Dev Dashboard → New Version → Release → reinstall the app on the merchant
store. After reinstall, the cached OAuth token must be invalidated so the next `get_valid_access_token`
mints a fresh one with the new scopes:

```sql
UPDATE `tabShopify Account` SET token_expires_at=NULL WHERE name='spqutq-ws.myshopify.com';
```

---

## Things to watch for

These came up during the session and could bite the next person.

1. **Save flows on Shopify Account re-trigger webhook registration.** Each save calls
   `_handle_webhooks`, which calls `register_webhooks` (which itself first calls
   `unregister_webhooks` to clear stale ones at the *current* `localtunnel_url`). If you save
   while the tunnel is down, registration will fail and the save throws. If the
   `localtunnel_url` has changed since last save, expect duplicate subscriptions.

2. **The Shopify Account's `company` field can drift unexpectedly** when a stale browser tab
   saves the form against an old snapshot. Hit `Ctrl+Shift+R` before saving if you've made
   DB-level changes recently.

3. **Frappe `item_defaults` auto-population** uses `frappe.defaults.get_defaults()` as fallback,
   pulling the `__default` company + warehouse. If those drift apart (e.g. company moves but
   default warehouse doesn't), every new Item creation breaks with "Warehouse X doesn't belong
   to Company Y". Keep `__default.company` and `__default.default_warehouse` in sync per site.

4. **`get_user_shopify_account` is the choke point.** Any user who hits a `temp_shopify_session`-decorated
   API endpoint must have `User Permission → Company` set, or the session resolves to `None`
   and the call silently no-ops. There's no helpful error.

5. **The `taxes` table is now optional** (we removed the constraint). But the integration *does*
   require **Default Sales Tax Account** and **Default Shipping Charges Account** at runtime —
   they aren't enforced at save time but the first order with a tax line will fail if these are
   blank. Set them.

6. **Frappe sessions cache the doctype JSON.** After editing `shopify_account.json`, you need
   `bench --site nexus.local migrate` AND a hard browser reload — old cached schema lingers
   otherwise.

---

## Open work (not done in this session)

| What | Why deferred |
|---|---|
| Update `ONBOARDING.md` to reflect: (a) Dev Dashboard is the only path now (legacy "Develop apps" page redirects), (b) the tax table is optional, (c) the bugs in this session that operators no longer have to work around | Will follow this session-notes commit |
| `~18` screenshots end-to-end against the EGP demo data | Pending |
| Frappe Module Onboarding wizard (6 steps in the Shopify workspace) | Pending |
| Shopify workspace itself (currently not present in the app) | Pending |
| Webhook idempotency improvement (diff before re-registering) | Nice-to-have |
| Cleaner currency handling (per-customer default currency on creation) | Nice-to-have |

---

## Credentials note

Live credentials (OAuth `client_id` / `client_secret`) for the Shopify app were pasted into
chat early in this session. The user said they were not real, but as a matter of habit
**any credential pasted into a chat transcript should be treated as compromised** and rotated.
That has been flagged; rotation is the user's call. No live credentials appear in this file
or in any committed code.
