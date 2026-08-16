# Technical Documentation: Shopify Null Product ID Handling

## Document Information
- **Version:** 1.0
- **Date:** December 16, 2025
- **Branch:** `fix/shopify-null-product-id-fallback-mapping`
- **Commit:** `eaf8d8a`
- **Author:** Development Team

---

## Table of Contents
1. [Problem Statement](#problem-statement)
2. [Solution Overview](#solution-overview)
3. [Files Modified](#files-modified)
4. [Detailed Code Changes](#detailed-code-changes)
5. [Function Reference](#function-reference)
6. [Data Flow](#data-flow)
7. [Testing Scenarios](#testing-scenarios)
8. [Dependencies](#dependencies)

---

## Problem Statement

### Issue
Shopify orders containing line items without a `product_id` (such as tips, samples, rush fees, adjustments) were failing to sync to ERPNext with the error:

```
expected String to be a id
```

### Root Causes
1. **Null Product ID Items**: Shopify allows line items without `product_id` for non-product charges (tips, samples, fees, adjustments)
2. **Missing Fallback Logic**: The system attempted to sync these items as regular products, causing API errors
3. **Early Filtering**: Items with null `product_id` were filtered out by `product_exists` check before reaching item mapping logic
4. **Duplicate Item Errors**: Existing items with missing Ecommerce Item link records caused duplicate errors that crashed the sync process

### Impact
- Orders with tips, samples, or fees failed to sync completely
- Sales Orders were not created for affected orders
- Manual intervention required for each failed order

---

## Solution Overview

### Approach
Implemented a multi-layered solution:

1. **Intelligent Fallback Mapping**: Map null `product_id` items to pre-configured fallback items based on line item title keywords
2. **Early Processing**: Process null `product_id` items before `product_exists` validation
3. **Error Handling**: Add comprehensive error handling to prevent sync failures
4. **Enhanced Matching**: Improve item matching to handle both SKU and product_id based item codes

### Fallback Item Mapping
| Line Item Title Contains | Mapped To ERPNext Item |
|-------------------------|------------------------|
| "tip" | `SHOPIFY-TIP` |
| "sample" | `SHOPIFY-SAMPLE` |
| "rush", "rush order", "rush fee" | `SHOPIFY-RUSH-FEE` |
| "adjustment", "price adjustment" | `SHOPIFY-ADJUSTMENT` |
| Any other text or empty | `SHOPIFY-MISC` |

**Note:** These fallback items must exist in ERPNext before deployment.

---

## Files Modified

### Summary
- **2 files modified**
- **~95 lines added**
- **~15 lines modified**

### File List
1. `ecommerce_integrations/shopify/product.py`
2. `ecommerce_integrations/shopify/order.py`

---

## Detailed Code Changes

### File 1: `ecommerce_integrations/shopify/product.py`

#### Change 1: New Function - `get_shopify_fallback_item()`

**Location:** Lines 22-46 (after imports, before `ShopifyProduct` class)

**Purpose:** Maps Shopify line item titles to appropriate ERPNext fallback items

**Code Added:**
```python
def get_shopify_fallback_item(title):
	"""Map Shopify line items without product_id to appropriate fallback items.
	
	Args:
		title (str): Line item title from Shopify
		
	Returns:
		str: ERPNext Item code
	"""
	if not title:
		return "SHOPIFY-MISC"
	
	title_lower = title.lower()
	
	# Map based on title keywords
	if "tip" in title_lower:
		return "SHOPIFY-TIP"
	elif "sample" in title_lower:
		return "SHOPIFY-SAMPLE"
	elif "rush" in title_lower or "rush order" in title_lower or "rush fee" in title_lower:
		return "SHOPIFY-RUSH-FEE"
	elif "adjustment" in title_lower or "price adjustment" in title_lower:
		return "SHOPIFY-ADJUSTMENT"
	else:
		return "SHOPIFY-MISC"
```

**Logic:**
- Case-insensitive keyword matching
- Priority order: tip → sample → rush → adjustment → misc
- Returns default `SHOPIFY-MISC` for empty titles or unmatched items

---

#### Change 2: Enhanced Function - `_match_sku_and_link_item()`

**Location:** Lines 301-351

**Purpose:** Match existing ERPNext items by both SKU and product_id to prevent duplicate creation

**Before:**
```python
def _match_sku_and_link_item(item_dict, product_id, variant_id, variant_of=None, has_variant=False) -> bool:
	"""Tries to match new item with existing item using Shopify SKU == item_code.

	Returns true if matched and linked.
	"""
	sku = item_dict["sku"]
	if not sku or variant_of or has_variant:
		return False

	item_name = frappe.db.get_value("Item", {"item_code": sku})
	if item_name:
		try:
			ecommerce_item = frappe.get_doc({...})
			ecommerce_item.insert()
			return True
		except Exception:
			return False
```

**After:**
```python
def _match_sku_and_link_item(item_dict, product_id, variant_id, variant_of=None, has_variant=False) -> bool:
	"""Tries to match new item with existing item using Shopify SKU == item_code or product_id.

	Returns true if matched and linked.
	"""
	sku = item_dict["sku"]
	if variant_of or has_variant:
		return False

	# Try matching by SKU first
	if sku:
		item_name = frappe.db.get_value("Item", {"item_code": sku})
		if item_name:
			try:
				ecommerce_item = frappe.get_doc({...})
				ecommerce_item.insert()
				return True
			except Exception:
				pass

	# Also try matching by product_id as item_code
	item_name = frappe.db.get_value("Item", {"item_code": product_id})
	if item_name:
		try:
			ecommerce_item = frappe.get_doc({...})
			ecommerce_item.insert()
			return True
		except Exception:
			return False

	return False
```

**Key Changes:**
1. Removed `if not sku` early return - now continues even without SKU
2. Added two-stage matching:
   - **Stage 1:** Match by SKU (original logic)
   - **Stage 2:** Match by product_id as item_code (new)
3. Better error handling with `pass` in Stage 1 to allow Stage 2 attempt

**Why:** Items created with product_id as item_code weren't found by SKU matching, causing duplicate errors.

---

#### Change 3: Enhanced Function - `create_items_if_not_exist()`

**Location:** Lines 354-376

**Purpose:** Skip syncing items with null product_id and add error handling

**Before:**
```python
def create_items_if_not_exist(order):
	"""Using shopify order, sync all items that are not already synced."""
	for item in order.get("line_items", []):
		product_id = item["product_id"]
		variant_id = item.get("variant_id")
		sku = item.get("sku")
		product = ShopifyProduct(product_id, variant_id=variant_id, sku=sku)

		if not product.is_synced():
			product.sync_product()
```

**After:**
```python
def create_items_if_not_exist(order):
	"""Using shopify order, sync all items that are not already synced."""
	for item in order.get("line_items", []):
		product_id = item.get("product_id")
		variant_id = item.get("variant_id")
		sku = item.get("sku")
		
		# Skip items with null product_id - mapped to fallback items
		if not product_id:
			continue
		
		try:
			product = ShopifyProduct(product_id, variant_id=variant_id, sku=sku)
			if not product.is_synced():
				product.sync_product()
		except frappe.DuplicateEntryError:
			frappe.logger().info(f"Item {product_id} already exists, skipping")
			continue
		except Exception as e:
			frappe.logger().error(f"Error syncing item {product_id}: {str(e)}")
			if "IntegrityError" not in str(e):
				raise
			continue
```

**Key Changes:**
1. Changed `item["product_id"]` to `item.get("product_id")` - handles null values
2. Added null check: `if not product_id: continue` - skips null product_id items
3. Wrapped sync logic in try-except block
4. Catches `DuplicateEntryError` - logs and continues
5. Catches `IntegrityError` - logs and continues (database constraint violations)
6. Re-raises other exceptions - real errors still propagate

**Why:** Prevents sync crashes when duplicate items exist or database constraints fail.

---

#### Change 4: Enhanced Function - `get_item_code()`

**Location:** Lines 379-412

**Purpose:** Handle null product_id items by mapping to fallback items

**Before:**
```python
def get_item_code(shopify_item):
	"""Get item code using shopify_item dict.

	Item should contain both product_id and variant_id."""

	item = ecommerce_item.get_erpnext_item(
		integration=MODULE_NAME,
		integration_item_code=shopify_item.get("product_id"),
		variant_id=shopify_item.get("variant_id"),
		sku=shopify_item.get("sku"),
	)
	if item:
		return item.item_code
```

**After:**
```python
def get_item_code(shopify_item):
	"""Get item code using shopify_item dict.

	Item should contain both product_id and variant_id."""
	
	product_id = shopify_item.get("product_id")
	variant_id = shopify_item.get("variant_id")
	sku = shopify_item.get("sku")
	title = shopify_item.get("title", "")
	
	# Handle items without product_id (tips, samples, fees)
	if not product_id:
		fallback_item = get_shopify_fallback_item(title)
		
		frappe.logger().info(
			f"Line item '{title}' has no product_id - mapped to: {fallback_item}"
		)
		
		if frappe.db.exists("Item", fallback_item):
			return fallback_item
		else:
			frappe.throw(
				f"Fallback item '{fallback_item}' not found for '{title}'"
			)
	
	# Original logic continues
	item = ecommerce_item.get_erpnext_item(
		integration=MODULE_NAME,
		integration_item_code=product_id,
		variant_id=variant_id,
		sku=sku,
	)
	if item:
		return item.item_code
```

**Key Changes:**
1. Extract `product_id`, `variant_id`, `sku`, and `title` at function start
2. Added null `product_id` check at beginning
3. Call `get_shopify_fallback_item(title)` for mapping
4. Log the mapping for debugging
5. Verify fallback item exists in ERPNext
6. Return fallback item code or throw error if not found
7. Original logic continues for items with valid `product_id`

**Why:** Provides intelligent mapping for non-product line items before they cause errors.

---

### File 2: `ecommerce_integrations/shopify/order.py`

#### Change 1: Enhanced Function - `get_order_items()`

**Location:** Lines 139-194

**Purpose:** Process null product_id items before product_exists validation

**Before:**
```python
def get_order_items(order_items, setting, delivery_date, taxes_inclusive):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append({...})
			continue

		if all_product_exists:
			item_code = get_item_code(shopify_item)
			items.append({...})
		else:
			items = []

	return items
```

**After:**
```python
def get_order_items(order_items, setting, delivery_date, taxes_inclusive):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		product_id = shopify_item.get("product_id")
		
		# Handle items without product_id (tips, samples, fees) - skip product_exists check
		if not product_id:
			item_code = get_item_code(shopify_item)
			if item_code:
				items.append(
					{
						"item_code": item_code,
						"item_name": shopify_item.get("name") or shopify_item.get("title"),
						"rate": _get_item_price(shopify_item, taxes_inclusive),
						"delivery_date": delivery_date,
						"qty": shopify_item.get("quantity"),
						"stock_uom": "Nos",
						"warehouse": setting.warehouse,
						ORDER_ITEM_DISCOUNT_FIELD: (
							_get_total_discount(shopify_item) / cint(shopify_item.get("quantity"))
						),
					}
				)
			continue
		
		# Original logic for items with product_id
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append({...})
			continue

		if all_product_exists:
			item_code = get_item_code(shopify_item)
			items.append({...})
		else:
			items = []

	return items
```

**Key Changes:**
1. Extract `product_id` at start of loop
2. Added null `product_id` check **before** `product_exists` check
3. For null `product_id`:
   - Call `get_item_code()` which routes to fallback mapping
   - Add item to list with proper structure
   - Use `shopify_item.get("name") or shopify_item.get("title")` for item_name
   - Use `"Nos"` as default stock_uom
   - Continue to next item (skip `product_exists` validation)
4. Original logic preserved for items with valid `product_id`

**Why:** Null `product_id` items were being filtered out by `product_exists` check before reaching the mapping logic.

---

## Function Reference

### New Functions

#### `get_shopify_fallback_item(title: str) -> str`
- **File:** `product.py`
- **Line:** 22-46
- **Parameters:**
  - `title` (str): Line item title from Shopify order
- **Returns:** ERPNext Item code (str)
- **Description:** Maps line item titles to fallback items using keyword matching
- **Dependencies:** None
- **Side Effects:** None

### Modified Functions

#### `_match_sku_and_link_item()`
- **File:** `product.py`
- **Line:** 301-351
- **Changes:** Two-stage matching (SKU + product_id)
- **Impact:** Prevents duplicate item creation errors

#### `create_items_if_not_exist()`
- **File:** `product.py`
- **Line:** 354-376
- **Changes:** Null check + error handling
- **Impact:** Skips null product_id items, handles duplicates gracefully

#### `get_item_code()`
- **File:** `product.py`
- **Line:** 379-412
- **Changes:** Null product_id handling with fallback mapping
- **Impact:** Maps non-product items to fallback items

#### `get_order_items()`
- **File:** `order.py`
- **Line:** 139-194
- **Changes:** Process null product_id items early
- **Impact:** Ensures null product_id items are included in Sales Orders

---

## Data Flow

### Order Sync Flow (Before)
```
Shopify Order Received
  ↓
sync_sales_order()
  ↓
create_items_if_not_exist()
  ↓
For each line_item:
  - product_id = item["product_id"]  ← CRASH if null
  - Try to sync product
  ↓
get_order_items()
  ↓
For each line_item:
  - Check product_exists  ← Filters out null product_id
  - get_item_code()  ← Never reached for null product_id
  ↓
Sales Order Created (incomplete)
```

### Order Sync Flow (After)
```
Shopify Order Received
  ↓
sync_sales_order()
  ↓
create_items_if_not_exist()
  ↓
For each line_item:
  - product_id = item.get("product_id")
  - if not product_id: continue  ← Skip syncing
  - Try to sync product
  - Catch duplicate errors → log & continue
  ↓
get_order_items()
  ↓
For each line_item:
  - product_id = shopify_item.get("product_id")
  - if not product_id:
      - get_item_code() → get_shopify_fallback_item()
      - Map to SHOPIFY-TIP/SAMPLE/etc.
      - Add to items list
      - continue
  - Original logic for valid product_id
  ↓
Sales Order Created (complete with all items)
```

### Fallback Mapping Flow
```
Line Item with null product_id
  ↓
get_item_code(shopify_item)
  ↓
Check: product_id is null?
  ↓ YES
get_shopify_fallback_item(title)
  ↓
Keyword Matching:
  - "tip" → SHOPIFY-TIP
  - "sample" → SHOPIFY-SAMPLE
  - "rush" → SHOPIFY-RUSH-FEE
  - "adjustment" → SHOPIFY-ADJUSTMENT
  - else → SHOPIFY-MISC
  ↓
Verify item exists in ERPNext
  ↓
Return item_code
```

---

## Testing Scenarios

### Test Case 1: Order with Tip
**Input:**
- Line Item 1: Product (product_id: 12345)
- Line Item 2: "Tip" (product_id: null)

**Expected Output:**
- Sales Order with 2 line items
- Item 1: Actual product item
- Item 2: SHOPIFY-TIP

**Test Command:**
```python
test_order_sync("43-31469-21")
```

### Test Case 2: Order with Sample
**Input:**
- Line Item 1: Product (product_id: 12345)
- Line Item 2: "Sample Fabric" (product_id: null)

**Expected Output:**
- Sales Order with 2 line items
- Item 1: Actual product item
- Item 2: SHOPIFY-SAMPLE

### Test Case 3: Order with Rush Fee
**Input:**
- Line Item 1: Product (product_id: 12345)
- Line Item 2: "Rush Order Fee" (product_id: null)

**Expected Output:**
- Sales Order with 2 line items
- Item 1: Actual product item
- Item 2: SHOPIFY-RUSH-FEE

### Test Case 4: Order with Multiple Non-Product Items
**Input:**
- Line Item 1: Product (product_id: 12345)
- Line Item 2: "Tip" (product_id: null)
- Line Item 3: "Rush Fee" (product_id: null)
- Line Item 4: "Order Adjustment" (product_id: null)

**Expected Output:**
- Sales Order with 4 line items
- All items mapped correctly

### Test Case 5: Duplicate Item Handling
**Input:**
- Order with existing item (product_id: 8706562621682)
- Ecommerce Item link missing

**Expected Output:**
- Item matched by product_id
- Ecommerce Item link created
- No duplicate error
- Order syncs successfully

### Test Case 6: Missing Fallback Item
**Input:**
- Order with "Tip" (product_id: null)
- SHOPIFY-TIP item not found in ERPNext

**Expected Output:**
- Error: "Fallback item 'SHOPIFY-TIP' not found for 'Tip'"
- Order sync fails with clear error message

---

## Dependencies

### Required ERPNext Items
The following items must exist in ERPNext before deployment:

1. **SHOPIFY-TIP**
   - Item Code: `SHOPIFY-TIP`
   - Item Name: "Customer Tip" (or similar)
   - Item Group: As per business requirements
   - Stock UOM: "Nos"

2. **SHOPIFY-SAMPLE**
   - Item Code: `SHOPIFY-SAMPLE`
   - Item Name: "Sample Item" (or similar)

3. **SHOPIFY-RUSH-FEE**
   - Item Code: `SHOPIFY-RUSH-FEE`
   - Item Name: "Rush Order Fee" (or similar)

4. **SHOPIFY-ADJUSTMENT**
   - Item Code: `SHOPIFY-ADJUSTMENT`
   - Item Name: "Order Adjustment" (or similar)

5. **SHOPIFY-MISC**
   - Item Code: `SHOPIFY-MISC`
   - Item Name: "Miscellaneous Charge" (or similar)

### Code Dependencies
- `frappe` - Core Frappe framework
- `ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item` - Ecommerce Item doctype
- `ecommerce_integrations.shopify.constants` - Module constants
- `ecommerce_integrations.shopify.utils` - Utility functions

### No Breaking Changes
- All changes are backward compatible
- Existing functionality preserved
- Only adds new handling for edge cases

---

## Deployment Checklist

- [ ] Verify all 5 fallback items exist in ERPNext
- [ ] Test with sample orders containing tips/samples/fees
- [ ] Verify error handling works for duplicate items
- [ ] Check logs for fallback mapping messages
- [ ] Test orders with mixed product and non-product items
- [ ] Verify Sales Orders are created with correct line items
- [ ] Monitor Integration Log for any new errors

---

## Rollback Procedure

If issues occur, restore from backup files:

```bash
cd apps/ecommerce_integrations/ecommerce_integrations/shopify
cp product.py.backup.YYYYMMDD_HHMM product.py
cp order.py.backup.YYYYMMDD_HHMM order.py
bench restart
```

---

## Support & Troubleshooting

### Common Issues

1. **"Fallback item 'SHOPIFY-XXX' not found"**
   - **Solution:** Create the missing fallback item in ERPNext

2. **Orders still failing with duplicate errors**
   - **Solution:** Check if `_match_sku_and_link_item()` is finding existing items correctly

3. **Null product_id items not appearing in Sales Order**
   - **Solution:** Verify `get_order_items()` is processing null items before `product_exists` check

### Logging

All fallback mappings are logged:
```
Line item 'Tip' has no product_id - mapped to: SHOPIFY-TIP
```

Check logs with:
```bash
bench --site [site] logs | grep -i "mapped to"
```

---

## Version History

- **v1.0** (2025-12-16): Initial implementation
  - Added fallback item mapping
  - Enhanced error handling
  - Improved item matching logic

---

## Contact

For questions or issues, refer to:
- GitHub Repository: `sahilvikas/ecommerce_integrations`
- Branch: `fix/shopify-null-product-id-fallback-mapping`
- Commit: `eaf8d8a`

