# ecommerce_integrations/unicommerce/inventory.py

from collections import defaultdict

import frappe
from frappe.utils import cint, now

from ecommerce_integrations.controllers.inventory import (
    get_inventory_levels,
    get_inventory_levels_of_group_warehouse,
    update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import MODULE_NAME, SETTINGS_DOCTYPE
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
    create_integration_log,
)

# Note: Undocumented but currently handles ~1000 inventory changes in one request.
MAX_INVENTORY_UPDATE_IN_REQUEST = 1000


def update_inventory_on_unicommerce(client=None, force=False):
    """Update ERPNext warehouse wise inventory to Unicommerce.
    
    LOGIC:
    1. Get all configured ERPNext warehouses
    2. For each warehouse, get current stock levels
    3. Map warehouse to Unicommerce facility
    4. Send stock updates to Unicommerce API
    5. Update sync status on success
    """
    log = None
    
    try:
        # CREATE LOG FIRST - ALWAYS
        try:
            log = create_integration_log(
                {
                    "integration": MODULE_NAME,
                    "method": "ecommerce_integrations.unicommerce.inventory.update_inventory_on_unicommerce",
                    "request_data": frappe.as_json({"force": force}),
                }
            )
            log.add_comment("Comment", f"📦 Inventory sync started (force={force})")
        except Exception as e:
            frappe.log_error(
                title="Inventory Sync - Log Creation Failed",
                message=f"{str(e)}\n\n{frappe.get_traceback()}"
            )
            # Dummy log that writes to Error Log
            log = frappe._dict({
                "add_comment": lambda t, m: frappe.log_error(title="Inventory Sync", message=m),
                "save": lambda: None
            })
            log.add_comment("Comment", "Using fallback logging")
        
        # Get settings
        settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
        
        # Check 1: Is integration enabled?
        if not settings.is_enabled():
            log.add_comment("Comment", "❌ EXIT: Integration disabled in settings")
            if log and hasattr(log, 'save'):
                log.save()
            return
        
        # Check 2: Is inventory sync enabled?
        if not settings.enable_inventory_sync:
            log.add_comment("Comment", "❌ EXIT: enable_inventory_sync checkbox is OFF")
            if log and hasattr(log, 'save'):
                log.save()
            return
        
        # Check 3: Should we run now? (based on frequency)
        if not force and not need_to_run(SETTINGS_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"):
            log.add_comment("Comment", "⏭ EXIT: Sync frequency not met (use force=True to override)")
            if log and hasattr(log, 'save'):
                log.save()
            return
        
        # Check 4: Get warehouses
        warehouses = settings.get_erpnext_warehouses()
        if not warehouses:
            log.add_comment("Comment", "❌ EXIT: No warehouses configured in settings")
            if log and hasattr(log, 'save'):
                log.save()
            return
        
        # Get warehouse to facility mapping
        wh_to_facility_map = settings.get_erpnext_to_integration_wh_mapping()
        
        log.add_comment("Comment", f"✓ Found {len(warehouses)} warehouse(s) to sync")
        
        # Initialize API client
        if client is None:
            client = UnicommerceAPIClient()
        
        # Tracking variables
        success_map = defaultdict(lambda: True)
        inventory_synced_on = now()
        total_items_processed = 0
        total_items_synced = 0
        warehouses_processed = 0
        warehouses_failed = 0
        
        # Process each warehouse
        for idx, warehouse in enumerate(warehouses, 1):
            try:
                log.add_comment("Comment", f"[{idx}/{len(warehouses)}] Processing warehouse: {warehouse}")
                
                # Get inventory levels
                is_group_warehouse = cint(frappe.db.get_value("Warehouse", warehouse, "is_group"))
                
                if is_group_warehouse:
                    erpnext_inventory = get_inventory_levels_of_group_warehouse(
                        warehouse=warehouse, integration=MODULE_NAME
                    )
                else:
                    erpnext_inventory = get_inventory_levels(
                        warehouses=(warehouse,), integration=MODULE_NAME
                    )
                
                if not erpnext_inventory:
                    log.add_comment("Comment", f"  → No items with stock in '{warehouse}'")
                    continue
                
                original_count = len(erpnext_inventory)
                erpnext_inventory = erpnext_inventory[:MAX_INVENTORY_UPDATE_IN_REQUEST]
                
                if original_count > MAX_INVENTORY_UPDATE_IN_REQUEST:
                    log.add_comment(
                        "Comment",
                        f"  → Limited to {MAX_INVENTORY_UPDATE_IN_REQUEST} items (total: {original_count})"
                    )
                
                total_items_processed += len(erpnext_inventory)
                
                # Build inventory map: {SKU: quantity}
                inventory_map = {d.integration_item_code: cint(d.actual_qty) for d in erpnext_inventory}
                
                # Get Unicommerce facility code
                facility_code = wh_to_facility_map.get(warehouse)
                if not facility_code:
                    log.add_comment("Comment", f"  → ❌ No facility code mapped for '{warehouse}'")
                    warehouses_failed += 1
                    continue
                
                log.add_comment("Comment", f"  → Syncing {len(inventory_map)} items to facility '{facility_code}'")
                
                # Send to Unicommerce
                response, status = client.bulk_inventory_update(
                    facility_code=facility_code, inventory_map=inventory_map
                )
                
                if status:
                    # Update success map
                    sku_to_ecom_item_map = {d.integration_item_code: d.ecom_item for d in erpnext_inventory}
                    warehouse_success_count = 0
                    
                    for sku, status_val in response.items():
                        ecom_item = sku_to_ecom_item_map.get(sku)
                        if ecom_item:
                            success_map[ecom_item] = success_map[ecom_item] and status_val
                            if status_val:
                                warehouse_success_count += 1
                    
                    total_items_synced += warehouse_success_count
                    warehouses_processed += 1
                    
                    log.add_comment(
                        "Comment",
                        f"  → ✓ Synced {warehouse_success_count}/{len(erpnext_inventory)} items"
                    )
                else:
                    log.add_comment("Comment", f"  → ❌ API returned failure")
                    warehouses_failed += 1
            
            except Exception as e:
                warehouses_failed += 1
                log.add_comment("Comment", f"  → ❌ ERROR: {str(e)}")
                frappe.log_error(
                    title=f"Inventory Sync Failed: {warehouse}",
                    message=frappe.get_traceback()
                )
                continue
        
        # Update sync status
        _update_inventory_sync_status(success_map, inventory_synced_on)
        
        # Update last sync time
        try:
            frappe.db.set_value(SETTINGS_DOCTYPE, settings.name, "last_inventory_sync", now())
        except Exception as e:
            frappe.log_error(title="Failed to update last_inventory_sync", message=frappe.get_traceback())
        
        # Final summary
        summary = (
            f"\n{'='*60}\n"
            f"INVENTORY SYNC COMPLETE\n"
            f"{'='*60}\n"
            f"Warehouses: {warehouses_processed} ✓ / {warehouses_failed} ✗\n"
            f"Items: {total_items_synced}/{total_items_processed} synced\n"
            f"{'='*60}"
        )
        
        if warehouses_failed > 0:
            log.add_comment("Comment", f"{summary}\n⚠ Completed with errors")
        else:
            log.add_comment("Comment", f"{summary}\n✓ Success")
        
        if log and hasattr(log, 'save'):
            log.save()
        
        frappe.db.commit()
    
    except Exception as e:
        error_trace = frappe.get_traceback()
        
        if log:
            log.add_comment("Comment", f"💥 CRITICAL ERROR:\n{error_trace}")
            if hasattr(log, 'save'):
                log.save()
        
        frappe.log_error(
            title="Unicommerce Inventory Sync - Critical Failure",
            message=error_trace
        )
        raise


def _update_inventory_sync_status(ecom_item_success_map, timestamp):
    """Update inventory sync status with per-item error handling."""
    for ecom_item, status in ecom_item_success_map.items():
        try:
            if status:
                update_inventory_sync_status(ecom_item, timestamp)
        except Exception as e:
            frappe.log_error(
                title=f"Failed to update sync status: {ecom_item}",
                message=frappe.get_traceback()
            )
            continue
