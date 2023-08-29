import frappe
from ecommerce_integrations.shopify.real_time_update import update_inventory_on_shopify_real_time

def update_stock_on_spf(self,method):
    shopify_settings = frappe.get_single("Shopify Setting")
    if shopify_settings.sync_realtime_inventory:
        if self.doctype in ["Purchase Receipt","Delivery Note","Stock Entry"] or (self.doctype in ["Purchase Invoice","Sales Invoice"] and self.update_stock == 1):
            update_inventory_on_shopify_real_time(self)



