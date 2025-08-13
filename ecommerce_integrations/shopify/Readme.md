# Shopify Integration for ERPNext

A **comprehensive, production-ready Shopify integration** for ERPNext, now with **multi-tenant** architecture — connect multiple Shopify stores to one ERPNext instance while ensuring complete **data isolation**, **company-specific configuration**, and **secure synchronization**.

---

## 🚀 Key Features

* **Multi-Tenant Architecture** – Connect multiple Shopify stores, each isolated to its own ERPNext company.
* **Legacy Compatibility** – Backward-compatible with existing single-store setups.
* **Company-Bound Data** – Warehouses, customers, and transactions are strictly company-specific.
* **Secure Webhook Handling** – Domain-based routing and HMAC validation for all inbound events.
* **Real-Time Synchronization** – Bidirectional sync for products, orders, inventory, and customers.
* **Granular Control** – Per-store toggles for product creation, invoice/fulfillment sync, and more.

---

## 🏗 Architecture

### Multi-Tenant Design

* **Shopify Account** *(Recommended)* – Modern, non-single DocType for per-store settings.
* **Shopify Setting** *(Deprecated)* – Legacy singleton for single-store setups.
* **Automatic Migration** – Legacy settings auto-migrate to new accounts on upgrade.

### Data Isolation

* Each **Shopify Account** is tied to one **ERPNext Company**.
* Warehouses, customers, and transactions are scoped to the company.
* No data leakage between stores — strict per-account mappings.

---

## ⚙️ Configuration

### 1. Multi-Tenant Setup *(Recommended)*

#### Create a Shopify Account

1. Go to **Shopify Account** in ERPNext.
2. Create a record with the following:

**Basic Info**

| Field         | Example                 | Notes                           |
| ------------- | ----------------------- | ------------------------------- |
| Enabled       | ✓                       | Activate this store integration |
| Account Title | Main Store KSA          | Friendly label                  |
| Shop Domain   | `mystore.myshopify.com` | Exact Shopify domain            |
| API Version   | `2023-10`               | Auto-managed                    |

**Credentials**

| Field          | Example          | Notes                       |
| -------------- | ---------------- | --------------------------- |
| Access Token   | `shpat_xxxxx`    | From Shopify Admin API      |
| Shared Secret  | `webhook-secret` | Used for HMAC validation    |
| Public App Key | optional         | Only for specific app flows |

**Company & Defaults**

| Field              | Example              | Notes                        |
| ------------------ | -------------------- | ---------------------------- |
| Company            | Your ERPNext Company | Required                     |
| Selling Price List | Standard Selling     | Optional                     |
| Cost Center        | Main – Company       | Required if SI/DN sync is on |
| Default Customer   | Walk-in Customer     | Fallback                     |

**Document Series**

| SO         | SI           | DN         |
| ---------- | ------------ | ---------- |
| `SO-SHOP-` | `SINV-SHOP-` | `DN-SHOP-` |

**Feature Toggles**

* Create Customers
* Create Missing Items
* Sync Sales Invoice
* Sync Delivery Note
* Allow Backdated Sync
* Close Orders on Fulfillment

**Product Upload Settings**

* Upload new ERPNext Items to Shopify
* Update Shopify Items on ERPNext changes
* Sync New Items as Active
* Upload Variants as Shopify Items

**Inventory Sync**

* Update ERPNext stock levels to Shopify
* Sync frequency (15min / 30min / Hourly / 6hrs / Daily)

**Old Orders Sync**

* One-time historical order sync with date range.

---

#### Warehouse Mappings

Map Shopify locations to ERPNext warehouses:

1. Fetch locations from Shopify.
2. Map each location to an ERPNext warehouse (must belong to the same company).

#### Tax Mappings

Map Shopify tax/shipping titles to ERPNext accounts:

* Shopify Tax/Shipping Title: e.g., `VAT`
* ERPNext Account: e.g., `VAT Payable – Company`

---

### 2. Legacy Setup *(Deprecated)*

* Only for existing single-store installations.
* Automatically migrated to **Shopify Account** during upgrade.

---

## 📦 Functional Areas

### 1. Product Management

* Bulk import via **Shopify Import Products** page.
* Account-aware upload and variant handling.
* SKU synchronization and price list integration.

### 2. Order Processing

* Webhook-driven real-time order creation.
* Auto-create customers with company isolation.
* Location-based inventory allocation.
* Tax mapping and shipping address handling.

**Supported Events:**
`orders/create`, `orders/updated`, `orders/paid`, `orders/cancelled`, `orders/fulfilled`, `orders/partially_fulfilled`

### 3. Fulfillment Management

* Auto-create Delivery Notes.
* Sync tracking numbers to Shopify.
* Multi-location fulfillment support.

### 4. Invoice Management

* Auto-create Sales Invoices for paid orders.
* Company-specific tax mapping and payment terms.

### 5. Inventory Synchronization

* Configurable sync frequency.
* Multi-location tracking.
* Warehouse-specific stock updates.

### 6. Customer Management

* Per-account customer creation and address sync.
* Company-specific customer groups.

---

## 🔔 Webhook Handling

### Automatic Setup

* Enabled accounts auto-register required webhooks in Shopify.
* Events routed by `X-Shopify-Shop-Domain`.
* HMAC validation per account.

**Monitored Events:**
`orders/create`, `orders/updated`, `orders/paid`, `orders/cancelled`, `orders/fulfilled`, `orders/partially_fulfilled`, `app/uninstalled`

---

## 📑 Custom Fields

**Item:** `shopify_selling_rate`
**Customer:** `shopify_customer_id`
**Supplier:** `shopify_supplier_id`
**Address:** `shopify_address_id`
**Sales Order:** `shopify_order_id`, `shopify_order_number`, `shopify_order_status`
**SO Item:** `shopify_discount_per_unit`
**Delivery Note:** `shopify_fulfillment_id`

---

## 🛡 Data Isolation Rules

1. **Warehouse must match account company.**
2. **Tax account must match account company.**
3. **Customers & transactions** created in the account’s company only.
4. **Series & numbering** respect the account’s settings.

---

## 🔄 Migration from Legacy

**Automatic Process:**

1. Detect existing **Shopify Setting**.
2. Create equivalent **Shopify Account**.
3. Validate data integrity.
4. Keep legacy mode as fallback until fully retired.

---

## 🛠 API Reference (Python)

```python
# Product
get_shopify_products(from_=None, account=None)
sync_product(product_id, account=None)
import_all_products(account=None)

# Orders
sync_sales_order(order_data, account=None)
create_order(order_data, account=None)

# Accounts
get_shopify_accounts()
validate_account(account_name)

# Inventory
update_inventory_levels(account=None)
sync_stock_to_shopify(account=None)
```

---

## 🧪 Testing Guidelines

* **Unit:** Function-level with mocks.
* **Integration:** End-to-end flows per account.
* **Multi-Tenant:** Two accounts → verify isolation.
* **Webhook:** Valid/invalid HMAC & domain routing.
* **Performance:** Large product/order datasets.

---

## 💡 Best Practices

**Account Setup**

* Use descriptive names.
* Map all locations and tax accounts.
* Test webhook connectivity before live.

**Warehouse**

* Keep consistent naming.
* Align warehouses to the account’s company.

**Security**

* Rotate tokens regularly.
* Monitor webhook logs.
* Use strong, unique secrets.

**Performance**

* Bulk operations for large catalogs.
* Tune inventory sync frequency.
* Monitor queue lengths.

---

## 📅 Maintenance

* Review integration logs weekly.
* Update Shopify API version periodically.
* Apply security patches promptly.
* Monitor API usage and sync times.

---

## 📚 Support

* **Docs:** This README + inline code comments.
* **Logs:** Integration Log, Error Log, Webhook Log.
* **Community:** ERPNext forums, GitHub Issues.
* **Professional:** ERPNext support or implementation partners.

---
