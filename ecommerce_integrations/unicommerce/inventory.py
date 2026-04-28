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
# Remaining to be done in next interval.
MAX_INVENTORY_UPDATE_IN_REQUEST = 1000


def update_inventory_on_unicommerce(client=None, force=False):
    """Update ERPNext warehouse wise inventory to Unicommerce.

    This function gets called by scheduler every minute. The function
    decides whether to run or not based on configured sync frequency.
    force=True ignores the set frequency.
    """
    log = None
    
    try:
        settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
        
        # Create integration log for debugging
        try:
            log = create_integration_log(
                {
                    "integration": MODULE_NAME,
                    "method": "ecommerce_integrations.unicommerce.inventory.update_inventory_on_unicommerce",
                    "request_data": frappe.as_json({"force": force}),
                }
            )
        except Exception as e:
            frappe.log_error(title="Failed to create inventory sync log", message=frappe.get_traceback())
            # Create dummy log to avoid crashes
            log = frappe._dict({
                "add_comment": lambda comment_type, text: None,
                "save": lambda: None
            })

        if not settings.is_enabled():
            if log:
                log.add_comment("Comment", "Unicommerce integration is disabled")
            return
            
        if not settings.enable_inventory_sync:
            if log:
                log.add_comment("Comment", "Inventory sync is disabled (enable_inventory_sync checkbox)")
            return

        # Check if need to run based on configured sync frequency
        if not force and not need_to_run(
            SETTINGS_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"
        ):
            if log:
                log.add_comment("Comment", "Skipped: sync frequency not met (use force=True to override)")
            return

        # Get configured warehouses
        warehouses = settings.get_erpnext_warehouses()
        if not warehouses:
            if log:
                log.add_comment("Comment", "No warehouses configured in Unicommerce Settings")
            return
            
        wh_to_facility_map = settings.get_erpnext_to_integration_wh_mapping()

        if client is None:
            client = UnicommerceAPIClient()

        # Track which ecommerce item was updated successfully
        success_map: dict[str, bool] = defaultdict(lambda: True)
        inventory_synced_on = now()
        
        total_items_processed = 0
        total_items_synced = 0
        warehouses_processed = 0
        warehouses_failed = 0

        if log:
            log.add_comment("Comment", f"Starting sync for {len(warehouses)} warehouse(s)")

        for warehouse in warehouses:
            try:
                is_group_warehouse = cint(frappe.db.get_value("Warehouse", warehouse, "is_group"))

                if is_group_warehouse:
                    erpnext_inventory = get_inventory_levels_of_group_warehouse(
                        warehouse=warehouse, integration=MODULE_NAME
                    )
                else:
                    erpnext_inventory = get_inventory_levels(warehouses=(warehouse,), integration=MODULE_NAME)

                if not erpnext_inventory:
                    if log:
                        log.add_comment("Comment", f"Warehouse '{warehouse}': No items to sync")
                    continue

                original_count = len(erpnext_inventory)
                erpnext_inventory = erpnext_inventory[:MAX_INVENTORY_UPDATE_IN_REQUEST]
                
                if original_count > MAX_INVENTORY_UPDATE_IN_REQUEST:
                    if log:
                        log.add_comment(
                            "Comment",
                            f"Warehouse '{warehouse}': Limited to {MAX_INVENTORY_UPDATE_IN_REQUEST} items "
                            f"(total: {original_count})"
                        )

                total_items_processed += len(erpnext_inventory)

                # TODO: consider reserved qty on both platforms.
                inventory_map = {d.integration_item_code: cint(d.actual_qty) for d in erpnext_inventory}
                facility_code = wh_to_facility_map.get(warehouse)
                
                if not facility_code:
                    if log:
                        log.add_comment("Comment", f"Warehouse '{warehouse}': No facility code mapped")
                    warehouses_failed += 1
                    continue

                response, status = client.bulk_inventory_update(
                    facility_code=facility_code, inventory_map=inventory_map
                )

                if status:
                    # Update success_map
                    sku_to_ecom_item_map = {d.integration_item_code: d.ecom_item for d in erpnext_inventory}
                    warehouse_success_count = 0
                    
                    for sku, status_val in response.items():
                        ecom_item = sku_to_ecom_item_map.get(sku)
                        if ecom_item:
                            # Any one warehouse sync failure should be considered failure
                            success_map[ecom_item] = success_map[ecom_item] and status_val
                            if status_val:
                                warehouse_success_count += 1
                    
                    total_items_synced += warehouse_success_count
                    warehouses_processed += 1
                    
                    if log:
                        log.add_comment(
                            "Comment",
                            f"Warehouse '{warehouse}' → Facility '{facility_code}': "
                            f"{warehouse_success_count}/{len(erpnext_inventory)} items synced"
                        )
                else:
                    if log:
                        log.add_comment("Comment", f"Warehouse '{warehouse}': API returned failure status")
                    warehouses_failed += 1
                    
            except Exception as e:
                warehouses_failed += 1
                error_msg = f"Warehouse '{warehouse}': {str(e)}"
                if log:
                    log.add_comment("Comment", error_msg)
                frappe.log_error(
                    title=f"Inventory Sync Failed for Warehouse: {warehouse}",
                    message=frappe.get_traceback()
                )
                # Continue with next warehouse
                continue

        # Update inventory sync status for all items
        _update_inventory_sync_status(success_map, inventory_synced_on)
        
        # Update last sync time in settings
        try:
            frappe.db.set_value(SETTINGS_DOCTYPE, settings.name, "last_inventory_sync", now())
        except Exception as e:
            frappe.log_error(title="Failed to update last_inventory_sync", message=frappe.get_traceback())
        
        # Final summary
        summary = (
            f"\n{'='*50}\n"
            f"SUMMARY\n"
            f"{'='*50}\n"
            f"Warehouses: {warehouses_processed} succeeded, {warehouses_failed} failed\n"
            f"Items: {total_items_synced}/{total_items_processed} synced\n"
            f"{'='*50}"
        )
        
        if log:
            if warehouses_failed > 0:
                log.add_comment("Comment", f"{summary}\n⚠ Completed with errors")
            else:
                log.add_comment("Comment", f"{summary}\n✓ All warehouses synced")
        
        frappe.db.commit()
        
    except Exception as e:
        if log:
            log.add_comment("Comment", frappe.get_traceback())
        
        frappe.log_error(
            title="Unicommerce Inventory Sync - Critical Failure",
            message=frappe.get_traceback()
        )
        raise


def _update_inventory_sync_status(ecom_item_success_map: dict[str, bool], timestamp: str) -> None:
    """Update inventory sync status with error handling for individual items."""
    for ecom_item, status in ecom_item_success_map.items():
        try:
            if status:
                update_inventory_sync_status(ecom_item, timestamp)
        except Exception as e:
            frappe.log_error(
                title=f"Failed to update inventory sync status: {ecom_item}",
                message=frappe.get_traceback()
            )
            # Continue with other items
            continue
