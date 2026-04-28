# ecommerce_integrations/unicommerce/inventory.py

from collections import defaultdict
from typing import Dict

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
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

# Note: Undocumented but currently handles ~1000 inventory changes in one request.
MAX_INVENTORY_UPDATE_IN_REQUEST = 1000


def update_inventory_on_unicommerce(client=None, force=False):
    """Update ERPNext warehouse wise inventory to Unicommerce.

    Called by scheduler every minute. Decides whether to run based on
    configured sync frequency. force=True ignores the set frequency.
    """

    # CREATE LOG FIRST - before any checks so we always have visibility
    log = create_unicommerce_log(
        status="Queued",
        message=f"Inventory sync triggered (force={force})",
        make_new=True,
    )

    try:
        settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)

        # Check 1: Integration enabled?
        if not settings.is_enabled():
            log.message = "EXIT: Unicommerce integration is disabled"
            log.status = "Failure"
            log.save()
            return

        # Check 2: Inventory sync enabled?
        if not settings.enable_inventory_sync:
            log.message = "EXIT: enable_inventory_sync checkbox is OFF in settings"
            log.status = "Failure"
            log.save()
            return

        # Check 3: Frequency check
        if not force and not need_to_run(
            SETTINGS_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"
        ):
            log.message = "SKIPPED: Sync frequency not met"
            log.status = "Success"
            log.save()
            return

        # Check 4: Warehouses configured?
        warehouses = settings.get_erpnext_warehouses()
        if not warehouses:
            log.message = "EXIT: No warehouses configured in Unicommerce Settings"
            log.status = "Failure"
            log.save()
            return

        wh_to_facility_map = settings.get_erpnext_to_integration_wh_mapping()

        if client is None:
            client = UnicommerceAPIClient()

        # Tracking
        success_map: Dict[str, bool] = defaultdict(lambda: True)
        inventory_synced_on = now()
        total_items_processed = 0
        total_items_synced = 0
        warehouses_processed = 0
        warehouses_failed = 0
        messages = [f"Starting sync for {len(warehouses)} warehouse(s)\n"]

        for idx, warehouse in enumerate(warehouses, 1):
            try:
                messages.append(f"[{idx}/{len(warehouses)}] Warehouse: {warehouse}")

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
                    messages.append(f"  → No items to sync")
                    continue

                original_count = len(erpnext_inventory)
                erpnext_inventory = erpnext_inventory[:MAX_INVENTORY_UPDATE_IN_REQUEST]

                if original_count > MAX_INVENTORY_UPDATE_IN_REQUEST:
                    messages.append(
                        f"  → Limited to {MAX_INVENTORY_UPDATE_IN_REQUEST} items (total: {original_count})"
                    )

                total_items_processed += len(erpnext_inventory)

                # Build {SKU: qty} map
                inventory_map = {d.integration_item_code: cint(d.actual_qty) for d in erpnext_inventory}

                # ✅ FIX: use .get() instead of [] to avoid KeyError
                facility_code = wh_to_facility_map.get(warehouse)
                if not facility_code:
                    messages.append(f"  → ❌ No facility code mapped for this warehouse")
                    warehouses_failed += 1
                    continue

                messages.append(f"  → Sending {len(inventory_map)} items to facility '{facility_code}'")

                response, status = client.bulk_inventory_update(
                    facility_code=facility_code, inventory_map=inventory_map
                )

                if status:
                    # ✅ FIX: use .get() instead of [] to avoid KeyError
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
                    messages.append(f"  → ✓ {warehouse_success_count}/{len(erpnext_inventory)} items synced")

                else:
                    messages.append(f"  → ❌ API returned failure")
                    warehouses_failed += 1

            except Exception as e:
                warehouses_failed += 1
                messages.append(f"  → ❌ ERROR: {str(e)}")
                frappe.log_error(
                    title=f"Inventory Sync Failed: {warehouse}",
                    message=frappe.get_traceback(),
                )
                continue

        # Update sync status
        _update_inventory_sync_status(success_map, inventory_synced_on)

        # Update last sync timestamp
        try:
            frappe.db.set_value(SETTINGS_DOCTYPE, settings.name, "last_inventory_sync", now())
        except Exception:
            frappe.log_error(title="Failed to update last_inventory_sync", message=frappe.get_traceback())

        # Final summary
        summary = (
            f"\n{'='*50}\n"
            f"SUMMARY\n"
            f"{'='*50}\n"
            f"Warehouses: {warehouses_processed} ✓  /  {warehouses_failed} ✗\n"
            f"Items: {total_items_synced} / {total_items_processed} synced\n"
            f"{'='*50}"
        )
        messages.append(summary)

        log.message = "\n".join(messages)
        log.status = "Success" if warehouses_failed == 0 else "Error"
        log.save()

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            title="Unicommerce Inventory Sync - Critical Failure",
            message=frappe.get_traceback(),
        )
        log.message = f"CRITICAL ERROR:\n{frappe.get_traceback()}"
        log.status = "Failure"
        log.save()
        raise


def _update_inventory_sync_status(ecom_item_success_map: Dict[str, bool], timestamp: str) -> None:
    """Update inventory sync status with per-item error handling."""
    for ecom_item, status in ecom_item_success_map.items():
        try:
            if status:
                update_inventory_sync_status(ecom_item, timestamp)
        except Exception:
            frappe.log_error(
                title=f"Failed to update sync status: {ecom_item}",
                message=frappe.get_traceback(),
            )
            continue
