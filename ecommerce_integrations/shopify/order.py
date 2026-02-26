"""
order.py - WITH COMPREHENSIVE STORE 2 LOGGING
This handles the background job for orders/create webhook.
"""

import json
import traceback
from typing import Literal, Optional

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate
from shopify.collection import PaginatedIterator
from shopify.resources import Order

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import (
    CUSTOMER_ID_FIELD,
    EVENT_MAPPER,
    ORDER_ID_FIELD,
    ORDER_ITEM_DISCOUNT_FIELD,
    ORDER_NUMBER_FIELD,
    ORDER_STATUS_FIELD,
    SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.product import create_items_if_not_exist, get_item_code
from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

DEFAULT_TAX_FIELDS = {
    "sales_tax": "default_sales_tax_account",
    "shipping": "default_shipping_charges_account",
}


def log_store2(step, message, store_name=None):
    """Helper function to log only for Store 2."""
    if store_name and store_name != "Store 1":
        frappe.log_error(
            title=f"[STORE2 ORDER] Step {step}",
            message=f"Store: {store_name}\n\n{message}"
        )


def sync_sales_order(payload, request_id=None, store_name=None):
    """Sync Shopify order to ERPNext Sales Order.
    
    This is called as a BACKGROUND JOB by the RQ worker.
    """
    order = payload
    
    # =========================================================================
    # STEP BG-1: Background job started
    # =========================================================================
    log_store2("BG-1", f"""
========================================
BACKGROUND JOB STARTED: sync_sales_order
========================================
request_id: {request_id}
store_name: {store_name}
Order ID: {order.get('id')}
Order Number: {order.get('name')}
frappe.local exists: {hasattr(frappe, 'local')}
""", store_name)
    
    frappe.set_user("Administrator")
    frappe.flags.request_id = request_id
    
    # =========================================================================
    # STEP BG-2: Set store context (CRITICAL!)
    # =========================================================================
    log_store2("BG-2", f"""
Setting store context in background worker...
Before: frappe.local.shopify_store_name = {getattr(frappe.local, 'shopify_store_name', 'NOT SET')}
""", store_name)
    
    if store_name:
        frappe.local.shopify_store_name = store_name
        log_store2("BG-2-OK", f"""
Store context set!
After: frappe.local.shopify_store_name = {frappe.local.shopify_store_name}
""", store_name)
    else:
        log_store2("BG-2-WARN", "store_name is None! Will default to Store 1 credentials!", store_name)
    
    # =========================================================================
    # STEP BG-3: Check if order already exists
    # =========================================================================
    log_store2("BG-3", f"Checking if Sales Order already exists for Shopify Order ID: {order['id']}", store_name)
    
    existing_so = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: cstr(order["id"])})
    
    if existing_so:
        log_store2("BG-3-SKIP", f"""
Sales Order already exists!
Existing SO: {existing_so}
Shopify Order ID: {order['id']}
Skipping creation.
""", store_name)
        create_shopify_log(status="Invalid", message="Sales order already exists, not synced")
        return
    
    log_store2("BG-3-OK", "No existing Sales Order found, proceeding with creation.", store_name)
    
    # =========================================================================
    # STEP BG-4: Process customer
    # =========================================================================
    try:
        log_store2("BG-4", "Processing customer...", store_name)
        
        shopify_customer = order.get("customer") if order.get("customer") is not None else {}
        shopify_customer["billing_address"] = order.get("billing_address", "")
        shopify_customer["shipping_address"] = order.get("shipping_address", "")
        customer_id = shopify_customer.get("id")
        
        log_store2("BG-4a", f"""
Customer data:
customer_id: {customer_id}
email: {shopify_customer.get('email')}
has billing_address: {bool(order.get('billing_address'))}
has shipping_address: {bool(order.get('shipping_address'))}
""", store_name)
        
        if customer_id:
            customer = ShopifyCustomer(customer_id=customer_id)
            if not customer.is_synced():
                log_store2("BG-4b", f"Customer {customer_id} not synced, creating...", store_name)
                customer.sync_customer(customer=shopify_customer)
                log_store2("BG-4c", f"Customer {customer_id} created.", store_name)
            else:
                log_store2("BG-4b", f"Customer {customer_id} already exists, updating addresses...", store_name)
                customer.update_existing_addresses(shopify_customer)
                log_store2("BG-4c", f"Customer {customer_id} addresses updated.", store_name)
        else:
            log_store2("BG-4-WARN", "No customer_id in order, will use default customer.", store_name)
        
        log_store2("BG-4-OK", "Customer processing complete.", store_name)
        
    except Exception as e:
        log_store2("BG-4-EXCEPTION", f"""
Exception processing customer!
Error: {str(e)}
Type: {type(e).__name__}

Traceback:
{traceback.format_exc()}
""", store_name)
        create_shopify_log(status="Error", exception=e, rollback=True)
        return
    
    # =========================================================================
    # STEP BG-5: Sync items/products
    # =========================================================================
    try:
        log_store2("BG-5", f"""
Syncing items from order...
Line items count: {len(order.get('line_items', []))}
Line items: {[item.get('title') for item in order.get('line_items', [])]}
""", store_name)
        
        create_items_if_not_exist(order)
        
        log_store2("BG-5-OK", "Items synced successfully.", store_name)
        
    except Exception as e:
        log_store2("BG-5-EXCEPTION", f"""
Exception syncing items!
Error: {str(e)}
Type: {type(e).__name__}

Traceback:
{traceback.format_exc()}
""", store_name)
        create_shopify_log(status="Error", exception=e, rollback=True)
        return
    
    # =========================================================================
    # STEP BG-6: Create Sales Order
    # =========================================================================
    try:
        log_store2("BG-6", "Creating Sales Order...", store_name)
        
        setting = frappe.get_doc(SETTING_DOCTYPE)
        
        log_store2("BG-6a", f"""
Settings loaded:
Company: {setting.company}
Warehouse: {setting.warehouse}
Sales Order Series: {setting.sales_order_series}
Default Customer: {setting.default_customer}
""", store_name)
        
        create_order(order, setting, store_name=store_name)
        
        log_store2("BG-6-OK", f"""
========================================
SALES ORDER CREATED SUCCESSFULLY!
========================================
Shopify Order ID: {order.get('id')}
Shopify Order Number: {order.get('name')}
Store: {store_name}
""", store_name)
        
    except Exception as e:
        log_store2("BG-6-EXCEPTION", f"""
Exception creating Sales Order!
Error: {str(e)}
Type: {type(e).__name__}

Traceback:
{traceback.format_exc()}
""", store_name)
        create_shopify_log(status="Error", exception=e, rollback=True)
        return
    
    # =========================================================================
    # STEP BG-7: Success!
    # =========================================================================
    log_store2("BG-7", "Creating success log entry...", store_name)
    create_shopify_log(status="Success")
    log_store2("BG-7-OK", f"""
========================================
BACKGROUND JOB COMPLETED SUCCESSFULLY!
========================================
Shopify Order: {order.get('name')}
Store: {store_name}
""", store_name)


def create_order(order, setting, company=None, store_name=None):
    """Create order with related documents."""
    # local import to avoid circular dependencies
    from ecommerce_integrations.shopify.fulfillment import create_delivery_note
    from ecommerce_integrations.shopify.invoice import create_sales_invoice

    log_store2("CREATE-1", "Inside create_order()", store_name)
    
    so = create_sales_order(order, setting, company, store_name=store_name)
    
    if so:
        log_store2("CREATE-2", f"Sales Order created: {so.name}", store_name)
        
        if order.get("financial_status") == "paid":
            log_store2("CREATE-3", "Order is paid, creating Sales Invoice...", store_name)
            create_sales_invoice(order, setting, so)
            log_store2("CREATE-3-OK", "Sales Invoice created.", store_name)

        if order.get("fulfillments"):
            log_store2("CREATE-4", "Order has fulfillments, creating Delivery Note...", store_name)
            create_delivery_note(order, setting, so)
            log_store2("CREATE-4-OK", "Delivery Note created.", store_name)
    else:
        log_store2("CREATE-WARN", "create_sales_order returned None!", store_name)


def create_sales_order(shopify_order, setting, company=None, store_name=None):
    """Create the actual Sales Order document."""
    
    log_store2("SO-1", f"""
Creating Sales Order...
Shopify Order ID: {shopify_order.get('id')}
Shopify Order Number: {shopify_order.get('name')}
""", store_name)
    
    # Determine customer
    customer = setting.default_customer
    if shopify_order.get("customer", {}):
        if customer_id := shopify_order.get("customer", {}).get("id"):
            customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name") or customer
    
    log_store2("SO-2", f"Customer determined: {customer}", store_name)
    
    # Check if SO already exists
    so = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")

    if not so:
        log_store2("SO-3", "Getting order items...", store_name)
        
        items = get_order_items(
            shopify_order.get("line_items"),
            setting,
            getdate(shopify_order.get("created_at")),
            taxes_inclusive=shopify_order.get("taxes_included"),
            store_name=store_name,
        )
        
        log_store2("SO-3a", f"Items count: {len(items)}", store_name)

        if not items:
            log_store2("SO-3-FAIL", "No items returned! Cannot create Sales Order.", store_name)
            message = (
                "Following items exists in the shopify order but relevant records were"
                " not found in the shopify Product master"
            )
            create_shopify_log(status="Error", exception=message, rollback=True)
            return ""

        log_store2("SO-4", "Getting order taxes...", store_name)
        taxes = get_order_taxes(shopify_order, setting, items)
        log_store2("SO-4a", f"Taxes count: {len(taxes)}", store_name)
        
        log_store2("SO-5", "Creating Sales Order document...", store_name)
        
        so = frappe.get_doc(
            {
                "doctype": "Sales Order",
                "naming_series": setting.sales_order_series or "SO-Shopify-",
                ORDER_ID_FIELD: str(shopify_order.get("id")),
                ORDER_NUMBER_FIELD: shopify_order.get("name"),
                "customer": customer,
                "transaction_date": getdate(shopify_order.get("created_at")) or nowdate(),
                "delivery_date": getdate(shopify_order.get("created_at")) or nowdate(),
                "company": setting.company,
                "selling_price_list": get_dummy_price_list(),
                "ignore_pricing_rule": 1,
                "items": items,
                "taxes": taxes,
                "tax_category": get_dummy_tax_category(),
            }
        )

        if company:
            so.update({"company": company, "status": "Draft"})
        
        so.flags.ignore_mandatory = True
        so.flags.shopiy_order_json = json.dumps(shopify_order)
        
        log_store2("SO-6", "Saving Sales Order...", store_name)
        so.save(ignore_permissions=True)
        log_store2("SO-6a", f"Sales Order saved: {so.name}", store_name)
        
        log_store2("SO-7", "Submitting Sales Order...", store_name)
        so.submit()
        log_store2("SO-7a", f"Sales Order submitted: {so.name}", store_name)

        if shopify_order.get("note"):
            so.add_comment(text=f"Order Note: {shopify_order.get('note')}")
            log_store2("SO-8", "Added order note as comment.", store_name)

    else:
        log_store2("SO-EXISTS", f"Sales Order already exists: {so}", store_name)
        so = frappe.get_doc("Sales Order", so)

    return so


def get_order_items(order_items, setting, delivery_date, taxes_inclusive, store_name=None):
    """Get order items for Sales Order."""
    items = []
    all_product_exists = True
    product_not_exists = []

    log_store2("ITEMS-1", f"Processing {len(order_items)} line items...", store_name)

    for idx, shopify_item in enumerate(order_items):
        product_id = shopify_item.get("product_id")
        
        log_store2(f"ITEMS-2-{idx}", f"""
Processing item {idx + 1}:
  title: {shopify_item.get('title')}
  product_id: {product_id}
  variant_id: {shopify_item.get('variant_id')}
  sku: {shopify_item.get('sku')}
  quantity: {shopify_item.get('quantity')}
  price: {shopify_item.get('price')}
  product_exists: {shopify_item.get('product_exists')}
""", store_name)
        
        # Handle items without product_id (tips, samples, fees)
        if not product_id:
            item_code = get_item_code(shopify_item)
            log_store2(f"ITEMS-2-{idx}-NOID", f"No product_id, mapped to: {item_code}", store_name)
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
            product_not_exists.append(
                {"title": shopify_item.get("title"), ORDER_ID_FIELD: shopify_item.get("id")}
            )
            log_store2(f"ITEMS-2-{idx}-NOTEXIST", f"Product does not exist in Shopify!", store_name)
            continue

        if all_product_exists:
            item_code = get_item_code(shopify_item)
            log_store2(f"ITEMS-2-{idx}-CODE", f"Item code: {item_code}", store_name)
            
            if not item_code:
                log_store2(f"ITEMS-2-{idx}-NOCODE", f"Could not get item_code!", store_name)
                continue
                
            items.append(
                {
                    "item_code": item_code,
                    "item_name": shopify_item.get("name"),
                    "rate": _get_item_price(shopify_item, taxes_inclusive),
                    "delivery_date": delivery_date,
                    "qty": shopify_item.get("quantity"),
                    "stock_uom": shopify_item.get("uom") or "Nos",
                    "warehouse": setting.warehouse,
                    ORDER_ITEM_DISCOUNT_FIELD: (
                        _get_total_discount(shopify_item) / cint(shopify_item.get("quantity"))
                    ),
                }
            )
        else:
            items = []

    log_store2("ITEMS-3", f"Returning {len(items)} items", store_name)
    return items


# Keep the rest of the functions unchanged but add store_name parameter where needed
def _get_item_price(line_item, taxes_inclusive: bool) -> float:
    price = flt(line_item.get("price"))
    qty = cint(line_item.get("quantity"))
    total_discount = _get_total_discount(line_item)

    if not taxes_inclusive:
        return price - (total_discount / qty)

    total_taxes = 0.0
    for tax in line_item.get("tax_lines"):
        total_taxes += flt(tax.get("price"))

    return price - (total_taxes + total_discount) / qty


def _get_total_discount(line_item) -> float:
    discount_allocations = line_item.get("discount_allocations") or []
    return sum(flt(discount.get("amount")) for discount in discount_allocations)


def get_order_taxes(shopify_order, setting, items):
    taxes = []
    line_items = shopify_order.get("line_items")

    for line_item in line_items:
        item_code = get_item_code(line_item)
        for tax in line_item.get("tax_lines"):
            taxes.append(
                {
                    "charge_type": "Actual",
                    "account_head": get_tax_account_head(tax, charge_type="sales_tax"),
                    "description": (
                        get_tax_account_description(tax)
                        or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
                    ),
                    "tax_amount": tax.get("price"),
                    "included_in_print_rate": 0,
                    "cost_center": setting.cost_center,
                    "item_wise_tax_detail": {item_code: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]},
                    "dont_recompute_tax": 1,
                }
            )

    update_taxes_with_shipping_lines(
        taxes,
        shopify_order.get("shipping_lines"),
        setting,
        items,
        taxes_inclusive=shopify_order.get("taxes_included"),
    )

    if cint(setting.consolidate_taxes):
        taxes = consolidate_order_taxes(taxes)

    for row in taxes:
        tax_detail = row.get("item_wise_tax_detail")
        if isinstance(tax_detail, dict):
            row["item_wise_tax_detail"] = json.dumps(tax_detail)

    return taxes


def consolidate_order_taxes(taxes):
    tax_account_wise_data = {}
    for tax in taxes:
        account_head = tax["account_head"]
        tax_account_wise_data.setdefault(
            account_head,
            {
                "charge_type": "Actual",
                "account_head": account_head,
                "description": tax.get("description"),
                "cost_center": tax.get("cost_center"),
                "included_in_print_rate": 0,
                "dont_recompute_tax": 1,
                "tax_amount": 0,
                "item_wise_tax_detail": {},
            },
        )
        tax_account_wise_data[account_head]["tax_amount"] += flt(tax.get("tax_amount"))
        if tax.get("item_wise_tax_detail"):
            tax_account_wise_data[account_head]["item_wise_tax_detail"].update(tax["item_wise_tax_detail"])

    return tax_account_wise_data.values()


def get_tax_account_head(tax, charge_type: Literal["shipping", "sales_tax"] | None = None):
    tax_title = str(tax.get("title"))

    tax_account = frappe.db.get_value(
        "Shopify Tax Account",
        {"parent": SETTING_DOCTYPE, "shopify_tax": tax_title},
        "tax_account",
    )

    if not tax_account and charge_type:
        tax_account = frappe.db.get_single_value(SETTING_DOCTYPE, DEFAULT_TAX_FIELDS[charge_type])

    if not tax_account:
        frappe.throw(_("Tax Account not specified for Shopify Tax {0}").format(tax.get("title")))

    return tax_account


def get_tax_account_description(tax):
    tax_title = tax.get("title")

    tax_description = frappe.db.get_value(
        "Shopify Tax Account",
        {"parent": SETTING_DOCTYPE, "shopify_tax": tax_title},
        "tax_description",
    )

    return tax_description


def update_taxes_with_shipping_lines(taxes, shipping_lines, setting, items, taxes_inclusive=False):
    shipping_as_item = cint(setting.add_shipping_as_item) and setting.shipping_item
    for shipping_charge in shipping_lines:
        if shipping_charge.get("price"):
            shipping_discounts = shipping_charge.get("discount_allocations") or []
            total_discount = sum(flt(discount.get("amount")) for discount in shipping_discounts)

            shipping_taxes = shipping_charge.get("tax_lines") or []
            total_tax = sum(flt(discount.get("price")) for discount in shipping_taxes)

            shipping_charge_amount = flt(shipping_charge["price"]) - flt(total_discount)
            if bool(taxes_inclusive):
                shipping_charge_amount -= total_tax

            if shipping_as_item:
                items.append(
                    {
                        "item_code": setting.shipping_item,
                        "rate": shipping_charge_amount,
                        "delivery_date": items[-1]["delivery_date"] if items else nowdate(),
                        "qty": 1,
                        "stock_uom": "Nos",
                        "warehouse": setting.warehouse,
                    }
                )
            else:
                taxes.append(
                    {
                        "charge_type": "Actual",
                        "account_head": get_tax_account_head(shipping_charge, charge_type="shipping"),
                        "description": get_tax_account_description(shipping_charge)
                        or shipping_charge["title"],
                        "tax_amount": shipping_charge_amount,
                        "cost_center": setting.cost_center,
                    }
                )

        for tax in shipping_charge.get("tax_lines"):
            taxes.append(
                {
                    "charge_type": "Actual",
                    "account_head": get_tax_account_head(tax, charge_type="sales_tax"),
                    "description": (
                        get_tax_account_description(tax)
                        or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
                    ),
                    "tax_amount": tax["price"],
                    "cost_center": setting.cost_center,
                    "item_wise_tax_detail": {
                        setting.shipping_item: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]
                    }
                    if shipping_as_item
                    else {},
                    "dont_recompute_tax": 1,
                }
            )


def get_sales_order(order_id):
    """Get ERPNext sales order using shopify order id."""
    sales_order = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: order_id})
    if sales_order:
        return frappe.get_doc("Sales Order", sales_order)


def cancel_order(payload, request_id=None, store_name=None):
    """Called by order/cancelled event."""
    frappe.set_user("Administrator")
    frappe.flags.request_id = request_id
    
    if store_name:
        frappe.local.shopify_store_name = store_name
        log_store2("CANCEL-1", f"Cancelling order {payload.get('id')}", store_name)

    order = payload

    try:
        order_id = order["id"]
        order_status = order["financial_status"]

        sales_order = get_sales_order(order_id)

        if not sales_order:
            log_store2("CANCEL-FAIL", f"Sales Order not found for {order_id}", store_name)
            create_shopify_log(status="Invalid", message="Sales Order does not exist")
            return

        sales_invoice = frappe.db.get_value("Sales Invoice", filters={ORDER_ID_FIELD: order_id})
        delivery_notes = frappe.db.get_list("Delivery Note", filters={ORDER_ID_FIELD: order_id})

        if sales_invoice:
            frappe.db.set_value("Sales Invoice", sales_invoice, ORDER_STATUS_FIELD, order_status)

        for dn in delivery_notes:
            frappe.db.set_value("Delivery Note", dn.name, ORDER_STATUS_FIELD, order_status)

        if not sales_invoice and not delivery_notes and sales_order.docstatus == 1:
            sales_order.cancel()
            log_store2("CANCEL-OK", f"Sales Order {sales_order.name} cancelled", store_name)
        else:
            frappe.db.set_value("Sales Order", sales_order.name, ORDER_STATUS_FIELD, order_status)
            log_store2("CANCEL-STATUS", f"Sales Order {sales_order.name} status updated", store_name)

    except Exception as e:
        log_store2("CANCEL-ERROR", f"Error: {str(e)}\n{traceback.format_exc()}", store_name)
        create_shopify_log(status="Error", exception=e)
    else:
        create_shopify_log(status="Success")


def update_sales_order(payload, request_id=None, store_name=None):
	"""Handle order updates from Shopify.
	
	Tracks essential changes: amounts, items, status, customer.
	Supports both Store 1 and Store 2.
	"""
	order = payload
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	
	# Set store context for Store 2
	if store_name:
		frappe.local.shopify_store_name = store_name
	
	try:
		order_id = cstr(order.get("id"))
		order_number = order.get("name", "")
		customer_id = order.get("customer", {}).get("id") if order.get("customer") else None
		
		# Check if Sales Order exists
		sales_order_name = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: order_id})
		
		if not sales_order_name:
			# Order doesn't exist, create it
			create_shopify_log(
				status="Info",
				message=f"Order {order_number} not found, creating new order",
				request_data=order,
				response_data={"action": "create_new_order"}
			)
			sync_sales_order(payload, request_id, store_name=store_name)
			return
		
		# Order exists, detect changes
		sales_order = frappe.get_doc("Sales Order", sales_order_name)
		changes = {}
		
		# Compare grand total (with tolerance for floating point)
		old_total = flt(sales_order.grand_total)
		new_total = flt(order.get("total_price", 0))
		if abs(old_total - new_total) > 0.01:  # Only if difference > 1 cent
			changes["grand_total"] = {
				"old": old_total,
				"new": new_total,
				"difference": new_total - old_total
			}
		
		# Compare subtotal (with tolerance)
		old_subtotal = flt(sales_order.total)
		new_subtotal = flt(order.get("subtotal_price", 0))
		if abs(old_subtotal - new_subtotal) > 0.01:  # Only if difference > 1 cent
			changes["subtotal"] = {
				"old": old_subtotal,
				"new": new_subtotal,
				"difference": new_subtotal - old_subtotal
			}
		
		# Compare financial status
		old_status = sales_order.get(ORDER_STATUS_FIELD) or ""
		new_status = order.get("financial_status", "")
		if old_status != new_status:
			changes["financial_status"] = {
				"old": old_status,
				"new": new_status
			}
		
		# Compare line items - only if shopify_line_item_id is available
		# If not available, skip line item comparison to avoid false positives
		line_item_changes = []
		shopify_items_map = {str(item.get("id")): item for item in order.get("line_items", [])}
		existing_shopify_ids = set()
		has_shopify_item_ids = False
		
		for so_item in sales_order.items:
			shopify_item_id = str(so_item.get("shopify_line_item_id", ""))
			if shopify_item_id:
				has_shopify_item_ids = True
				if shopify_item_id in shopify_items_map:
					shopify_item = shopify_items_map[shopify_item_id]
					item_changes = {}
					
					# Compare quantity
					old_qty = cint(so_item.qty)
					new_qty = cint(shopify_item.get("quantity", 0))
					if old_qty != new_qty:
						item_changes["quantity"] = {"old": old_qty, "new": new_qty}
					
					# Compare rate (with tolerance)
					old_rate = flt(so_item.rate)
					new_rate = flt(_get_item_price(shopify_item, order.get("taxes_included", False)))
					if abs(old_rate - new_rate) > 0.01:
						item_changes["rate"] = {"old": old_rate, "new": new_rate}
					
					if item_changes:
						line_item_changes.append({
							"item_code": so_item.item_code,
							"changes": item_changes
						})
					
					existing_shopify_ids.add(shopify_item_id)
		
		# Only check for new items if we have shopify_line_item_id stored
		# Otherwise, skip to avoid false positives
		if has_shopify_item_ids:
			for shopify_item in order.get("line_items", []):
				if str(shopify_item.get("id")) not in existing_shopify_ids:
					line_item_changes.append({
						"title": shopify_item.get("title"),
						"action": "added"
					})
		
		if line_item_changes:
			changes["line_items"] = line_item_changes
		
		# Compare customer
		old_customer = sales_order.customer
		if customer_id:
			new_customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name")
			if old_customer != new_customer and new_customer:
				changes["customer"] = {
					"old": old_customer,
					"new": new_customer
				}
		
		# REMOVED: Transaction date comparison - created_at never changes, causes false positives
		# Shopify sends updated_at changes which trigger webhooks but don't affect business data
		
		# Skip logging if no changes detected
		if not changes:
			return
		
		# Process the update
		if sales_order.docstatus == 2:  # Cancelled
			frappe.db.set_value("Sales Order", sales_order_name, ORDER_STATUS_FIELD, order.get("financial_status"))
			create_shopify_log(
				status="Invalid",
				message=f"Cannot update cancelled Sales Order {sales_order_name}. Order {order_number} status updated.",
				request_data=order,
				response_data={"change_details": changes}
			)
			return
		
		# Update customer if changed
		shopify_customer = order.get("customer") if order.get("customer") is not None else {}
		shopify_customer["billing_address"] = order.get("billing_address", "")
		shopify_customer["shipping_address"] = order.get("shipping_address", "")
		
		if customer_id:
			customer = ShopifyCustomer(customer_id=customer_id)
			if not customer.is_synced():
				customer.sync_customer(customer=shopify_customer)
			else:
				customer.update_existing_addresses(shopify_customer)
		
		# Ensure items exist
		create_items_if_not_exist(order)
		
		# Get updated items and taxes
		setting = frappe.get_doc(SETTING_DOCTYPE)
		items = get_order_items(
			order.get("line_items"),
			setting,
			getdate(order.get("created_at")) or getdate(sales_order.transaction_date),
			taxes_inclusive=order.get("taxes_included"),
			store_name=store_name,
		)
		
		if not items:
			create_shopify_log(
				status="Error",
				message="Cannot update order: items not found in product master",
				request_data=order,
				response_data={"change_details": changes},
				rollback=True
			)
			return
		
		taxes = get_order_taxes(order, setting, items)
		
		# Update customer name
		customer_name = setting.default_customer
		if shopify_customer.get("id"):
			customer_name = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name") or setting.default_customer
		
		# Determine if we have real business changes (not just metadata)
		has_real_changes = any(
			key in changes for key in ["grand_total", "subtotal", "financial_status", "line_items", "customer"]
		)
		
		# Update the order
		if sales_order.docstatus == 1:  # Submitted
			# For submitted orders, only update status and notes if there are REAL changes
			# Don't log if only metadata changed (fulfillment status, tracking, updated_at, etc.)
			if has_real_changes:
				frappe.db.set_value("Sales Order", sales_order_name, ORDER_STATUS_FIELD, order.get("financial_status"))
				
				if order.get("note"):
					sales_order.add_comment(text=f"Order Note Updated: {order.get('note')}")
				
				create_shopify_log(
					status="Success",
					message=f"Order {order_number} updated (status and notes only). Items/taxes require manual update.",
					request_data=order,
					response_data={"change_details": changes}
				)
			else:
				# If no real changes, silently return (metadata-only update)
				return
		else:
			# Draft order - can update fully
			sales_order.update({
				"customer": customer_name,
				"transaction_date": getdate(order.get("created_at")) or sales_order.transaction_date,
				"delivery_date": getdate(order.get("created_at")) or sales_order.delivery_date,
				"items": items,
				"taxes": taxes,
			})
			
			if order.get("name") != sales_order.get(ORDER_NUMBER_FIELD):
				sales_order.set(ORDER_NUMBER_FIELD, order.get("name"))
			
			sales_order.flags.ignore_mandatory = True
			sales_order.flags.shopiy_order_json = json.dumps(order)
			sales_order.save(ignore_permissions=True)
			
			if order.get("note"):
				sales_order.add_comment(text=f"Order Note: {order.get('note')}")
			
			create_shopify_log(
				status="Success",
				message=f"Order {order_number} updated successfully with {len(changes)} change(s) detected",
				request_data=order,
				response_data={"change_details": changes}
			)

		# Trigger update detection on all linked documents (only if real changes detected)
		if sales_order_name and has_real_changes:
			try:
				old_form_dict = dict(frappe.form_dict)
				frappe.form_dict["doctype"] = "Sales Order"
				frappe.form_dict["docname"] = sales_order_name

				if frappe.db.exists("Server Script", "Get Shopify Order Updates"):
					script_doc = frappe.get_doc("Server Script", "Get Shopify Order Updates")
					script_doc.execute_method()

					detection_result = frappe.response.get("message") or {}
					detection_has_changes = detection_result.get("has_changes", False)

					if detection_has_changes:
						frappe.logger().info(
							"Shopify update detection: Changes flagged on all linked docs for SO {}".format(
								sales_order_name
							)
						)

				frappe.form_dict = old_form_dict
			except Exception as detection_error:
				frappe.logger().error(
					"Shopify update detection failed for SO {}: {}".format(
						sales_order_name, str(detection_error)
					)
				)
				frappe.form_dict = old_form_dict if "old_form_dict" in locals() else frappe.form_dict
		
	except Exception as e:
		create_shopify_log(
			status="Error",
			exception=e,
			message=f"Failed to update order {order.get('name', 'Unknown')}: {str(e)}",
			request_data=order,
			response_data={"error": str(e)},
			rollback=True
		)


@temp_shopify_session
def sync_old_orders():
    shopify_setting = frappe.get_cached_doc(SETTING_DOCTYPE)
    if not cint(shopify_setting.sync_old_orders):
        return

    orders = _fetch_old_orders(shopify_setting.old_orders_from, shopify_setting.old_orders_to)

    for order in orders:
        log = create_shopify_log(
            method=EVENT_MAPPER["orders/create"], request_data=json.dumps(order), make_new=True
        )
        sync_sales_order(order, request_id=log.name)

    shopify_setting = frappe.get_doc(SETTING_DOCTYPE)
    shopify_setting.sync_old_orders = 0
    shopify_setting.save()


def _fetch_old_orders(from_time, to_time):
    from_time = get_datetime(from_time).astimezone().isoformat()
    to_time = get_datetime(to_time).astimezone().isoformat()
    orders_iterator = PaginatedIterator(
        Order.find(created_at_min=from_time, created_at_max=to_time, limit=250)
    )

    for orders in orders_iterator:
        for order in orders:
            yield order.to_dict()