<div align="center">
    <img src="https://frappecloud.com/files/ERPNext%20-%20Ecommerce%20Integrations.png" height="128">
    <h2>Ecommerce Integrations for ERPNext</h2>

[![CI](https://github.com/frappe/ecommerce_integrations/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/ecommerce_integrations/actions/workflows/ci.yml)
  
</div>

### Currently supported integrations:

- Shopify - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/shopify_integration)
- Unicommerce - [User Documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/unicommerce_integration)
- Zenoti - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/zenoti_integration)
- Amazon - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/amazon_integration)


### Installation

- Frappe Cloud Users can install [from Marketplace](https://frappecloud.com/marketplace/apps/ecommerce_integrations).
- Self Hosted users can install using Bench:

```bash
# Production installation
$ bench get-app ecommerce_integrations --branch main

# OR development install
$ bench get-app ecommerce_integrations  --branch develop

# install on site
$ bench --site sitename install-app ecommerce_integrations
```

After installation follow user documentation for each integration to set it up.

### Contributing

- Follow general [ERPNext contribution guideline](https://github.com/frappe/erpnext/wiki/Contribution-Guidelines)
- Send PRs to `develop` branch only.

### Development setup

- Enable developer mode.
- If you want to use a tunnel for local development. Set `localtunnel_url` parameter in your site_config file with ngrok / localtunnel URL. This will be used in most places to register webhooks. Likewise, use this parameter wherever you're sending current site URL to integrations in development mode.

#### License

GNU GPL v3.0

## Shopify Multi-Shop Support Plan

- **Current Constraints**
  - `Shopify Setting` is a singleton (`ecommerce_integrations/shopify/doctype/shopify_setting/shopify_setting.json:393`), so every call to `frappe.get_doc(SETTING_DOCTYPE)` throughout the Shopify stack (`ecommerce_integrations/shopify/connection.py:30`, `ecommerce_integrations/shopify/order.py:54`, `ecommerce_integrations/shopify/customer.py:18`, `ecommerce_integrations/shopify/inventory.py:23`, `ecommerce_integrations/shopify/product.py:344`, `ecommerce_integrations/shopify/fulfillment.py:19`, `ecommerce_integrations/shopify/invoice.py:19`) always returns the same configuration.
  - Incoming webhook handling (`ecommerce_integrations/shopify/connection.py:94`) never inspects `X-Shopify-Shop-Domain`; signature validation (`ecommerce_integrations/shopify/connection.py:121`) and job enqueuing (`ecommerce_integrations/shopify/connection.py:107`) assume a single shared secret and never pass shop context onward.
  - Scheduler hooks (`ecommerce_integrations/hooks.py:139`) and helper utilities (`ecommerce_integrations/controllers/scheduling.py:7`) only support one interval/timestamp pair because they rely on singleton access.
  - Test fixtures and utilities (`ecommerce_integrations/shopify/tests/utils.py:44`) patch one global settings doc, reinforcing the single-shop assumption across unit coverage.

- **Target Architecture**
  - Represent each Shopify store with an individual `Shopify Setting` document (non-singleton) keyed by its shop domain; keep existing child tables (warehouse mapping, taxes, webhooks) scoped to each document.
  - Inject shop context into every integration boundary: webhook entry and background jobs, scheduled jobs, and Desk-triggered hooks should all operate on a known `Shopify Setting` record.
  - Centralise session handling so calls into the Shopify API establish a session for the specific shop (e.g. pass a `ShopifySetting` instance into `temp_shopify_session`).
  - Extend observability to track which shop produced each `Ecommerce Integration Log` entry and to surface per-shop enablement toggles in the UI.

- **Implementation Steps**
  1. *Data model refactor* – Flip `Shopify Setting` to a standard DocType, add a unique `shop_domain` (normalized) field, and update form scripts to support list+form flows instead of singleton editing.
  2. *Session/context plumbing* – Refactor `temp_shopify_session` (`ecommerce_integrations/shopify/connection.py:21`) to accept an explicit `ShopifySetting` (from args/kwargs or `frappe.flags`) and fall back gracefully when exactly one store exists. Update helpers that cache the doc to accept an identifier rather than calling `frappe.get_doc` implicitly.
  3. *Webhook routing* – Capture `X-Shopify-Shop-Domain` inside `store_request_data`, look up the correct setting, validate against its `shared_secret`, stash shop info on the log, and enqueue downstream jobs with `shopify_setting` (name/domain) in kwargs so handlers can reload the right configuration.
  4. *Handler updates* – Update order, invoice, fulfillment, customer, and product flows to expect a setting identifier (or doc) and to pass it along when they call other helpers. Replace global `frappe.get_doc` calls with context-aware fetches; adjust `ShopifyCustomer` to receive the setting during init.
  5. *Scheduler and cron jobs* – Change `update_inventory_on_shopify`, `sync_old_orders`, and any other scheduled Shopify tasks to iterate over enabled settings, invoking the job per shop while respecting each record’s frequency fields (`inventory_sync_frequency`, `last_inventory_sync`).
  6. *Desk and hook behaviour* – When ERPNext `Item` hooks fire (`ecommerce_integrations/shopify/product.py:331`), decide whether to sync to all enabled shops or introduce per-shop inclusion rules; enqueue per-shop jobs so the Shopify session decorator can target the right credentials.
  7. *Logging & UX* – Add a `reference_doc` (or similar) to `Ecommerce Integration Log` so operators can filter by shop; update Desk views/buttons (e.g. in `shopify_setting.js`) to handle multi-record operations.
  8. *Migration & cleanup* – Ship a patch that migrates existing singleton data into a new `Shopify Setting` record, rewires related child tables, and drops the legacy singleton row. Update automated tests to create multiple settings and assert that routing logic picks the correct one.

- **Validation & Rollout**
  - Regression-test all webhook flows with multiple mocked shop domains (orders, fulfillments, cancellations) to ensure context is preserved end-to-end.
  - Exercise scheduler jobs with staggered `inventory_sync_frequency` values to confirm per-shop throttling works.
  - Verify ERPNext item upload/edit hooks push to the intended shop(s) and document any deliberate scope changes (e.g. broadcasting to all shops vs. per-shop opt-in).
  - Provide upgrade guidance: sequence to apply migrations, re-authorise webhooks for each shop, and reconfigure any automation that assumed a singleton settings record.
