import frappe
from ecommerce_integrations.unicommerce.constants import (
    CHANNEL_ID_FIELD,
    CHANNEL_TAX_ACCOUNT_FIELD_MAP,
    FACILITY_CODE_FIELD,
    IS_COD_CHECKBOX,
    MODULE_NAME,
    ORDER_CODE_FIELD,
    ORDER_ITEM_BATCH_NO,
    ORDER_ITEM_CODE_FIELD,
    ORDER_STATUS_FIELD,
    PACKAGE_TYPE_FIELD,
    SETTINGS_DOCTYPE,
    TAX_FIELDS_MAPPING,
    TAX_RATE_FIELDS_MAPPING,
)
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.order import _get_new_orders
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date
SHIPMENT_STATES = [
    "CREATED",
    "LOCATION_NOT_SERVICEABLE",
    "PICKING",
    "PICKED",
    "PACKED",
    "READY_TO_SHIP",
    "CANCELLED",
    "MANIFESTED",
    "DISPATCHED",
    "SHIPPED",
    "DELIVERED",
    "PENDING_CUSTOMIZATION",
    "CUSTOMIZATION_COMPLETE",
    "RETURN_EXPECTED",
    "RETURNED",
    "SPLITTED",
    "RETURN_ACKNOWLEDGED",
    "MERGED",
]
import time
def prepare_delivery_note():
    try:
        time.sleep(15)
        settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
        client = UnicommerceAPIClient()

        days_to_sync = min(settings.get("order_status_days") or 2, 14)
        minutes = days_to_sync * 24 * 60

        # find all Facilities
        enabled_facilities = list(settings.get_integration_to_erpnext_wh_mapping().keys())
        enabled_channels = frappe.db.get_list(
            "Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id"
        )
        for facility in enabled_facilities:
            updated_packages = client.search_shipping_packages(updated_since=minutes, facility_code=facility)
            valid_packages = [p for p in updated_packages if p.get("channel") in enabled_channels]
            if not valid_packages:
                continue
            shipped_packages = [p for p in valid_packages if p["status"] in ["DISPATCHED"]]
            for order in shipped_packages:
                sales_order = frappe.get_doc("Sales Order", {ORDER_CODE_FIELD: order["saleOrderCode"]})
                if sales_order:
                    create_delivery_note(order, settings, sales_order)
                    create_unicommerce_log(status="Success")
    except Exception as e:
        create_unicommerce_log(status="Error", exception=e, rollback=True)


def create_delivery_note(order, settings, so):
    try:
        if (
            not frappe.db.get_value("Delivery Note", {"shipment_id":order["code"]}, "name")
        ):  
            
            frappe.logger("log1").exception("++++++++++++++++++++++"+str(so.unicommerce_order_code))
            # Get the sales invoice
            if frappe.get_value("Sales Invoice", {"unicommerce_order_code":so.unicommerce_order_code}):
                sales_invoice = frappe.get_doc("Sales Invoice", {"unicommerce_order_code":so.unicommerce_order_code})

                # Create the delivery note
                delivery_note = frappe.new_doc("Delivery Note")
                delivery_note.update({
                    "customer": sales_invoice.customer,
                    "customer_address": sales_invoice.customer_address,
                    "shipping_address_name": sales_invoice.shipping_address_name,
                    "posting_date": sales_invoice.posting_date,
                    "items": []
                })

                # Add items to the delivery note
                for item in sales_invoice.items:
                    delivery_note.append("items", {
                        "item_code": item.item_code,
                        "item_name": item.item_name,
                        "description": item.description,
                        "qty": item.qty,
                        "uom": item.uom,
                        "rate": item.rate,
                        "amount": item.amount,
                        "stock_qty": item.qty,
                        "so_detail": item.name,
                        "so_detail_item": item.idx,
                        "warehouse":item.warehouse,
                        "against_sales_order":so.name
                    })
                for item in sales_invoice.taxes:
                    delivery_note.append("taxes",{
                    "charge_type": item.charge_type,
                    "account_head": item.account_head,
                    "tax_amount": item.tax_amount,
                    "description": item.description,
                    "item_wise_tax_detail": item.item_wise_tax_detail,
                    "dont_recompute_tax": item.dont_recompute_tax,
                    })
                # Save the delivery note
                delivery_note.flags.ignore_permissions = True
                delivery_note.flags.ignore_mandatory = True
                delivery_note.unicommerce_order_no = order["saleOrderCode"]
                delivery_note.shipment_id = order["code"] 
                delivery_note.status = "Completed"
                delivery_note.insert()

                # Submit the delivery note
                delivery_note.submit()

                # Update the sales invoice with the delivery note information
                sales_invoice.delivery_note = delivery_note.name
                sales_invoice.flags.ignore_permissions = True
                sales_invoice.flags.ignore_mandatory = True
                sales_invoice.save()


                # create a new delivery note
                
                # delivery_note = frappe.get_doc({
                # "doctype": "Delivery Note",
                # "customer": so.customer,
                # "unicommerce_order_no":order["saleOrderCode"],
                # "shipment_id":order["code"],
                # "status": "Completed"
                # })

                # # add items to the delivery note
                # for item in so.items:
                #     delivery_note.append("items", {
                #     "item_code": item.item_code,
                #     "qty": item.qty,
                #     "rate": item.rate,
                #     "warehouse":item.warehouse,
                #     "against_sales_order":so.name
                #     })
                # # add texes to the delivery note   
                # for item in so.taxes:
                #     delivery_note.append("taxes",{
                #     "charge_type": item.charge_type,
                #     "account_head": item.account_head,
                #     "tax_amount": item.tax_amount,
                #     "description": item.description,
                #     "item_wise_tax_detail": item.item_wise_tax_detail,
                #     "dont_recompute_tax": item.dont_recompute_tax,
                #     })

                # # save the delivery note
                # delivery_note.insert()
                # delivery_note.submit()

    except Exception as e:
        frappe.logger("log1").exception(e)
        create_unicommerce_log(status="Error", exception=e, rollback=True)
