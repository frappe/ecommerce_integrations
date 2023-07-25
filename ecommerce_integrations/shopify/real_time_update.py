import frappe
from frappe.utils import now
from ecommerce_integrations.shopify.connection import temp_shopify_session
from frappe.utils import cint, create_batch, now
from shopify.resources import InventoryLevel, Variant
from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	update_inventory_sync_status,
)
from pyactiveresource.connection import ResourceNotFound

from ecommerce_integrations.shopify.inventory import _log_inventory_update_status
from ecommerce_integrations.shopify.theme_template import update_item_theme_template,is_ecommerce_item


def update_inventory_on_shopify_real_time(doc):
	"""Upload stock levels from ERPNext to Shopify.

	Called by scheduler on configured interval.
	"""
	
	setting = frappe.get_doc("Shopify Setting")

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		
		return
	
	warehous_map = setting.get_erpnext_to_integration_wh_mapping()
	# frappe.throw(str(warehous_map))
	inventory_levels = get_doc_items_level(doc)

	upload_inventory_data_to_shopify(inventory_levels, warehous_map)
	
	update_theme_template(inventory_levels)


def update_theme_template(invetory_levels):
	
	for item in invetory_levels:
		if is_ecommerce_item(item['item_code']):		
			if item["actual_qty"] == 0:
			
				stock_from_other_warehouses = frappe.db.sql(
						"""
						SELECT sum(actual_qty) as total_qty
						FROM `tabBin`
						WHERE
							item_code = %(item)s
						GROUP BY item_code
						""",
						{
							"item": item['item_code'],
						},
						as_dict=1,
					)
				
				if len(stock_from_other_warehouses) > 0 and stock_from_other_warehouses[0]['total_qty'] == 0.0:
					
					update_item_theme_template(item['item_code'],1)
			else:
				update_item_theme_template(item['item_code'])

	

def get_doc_items_level(doc):
	# frappe.throw(str(doc.name))
	inventory_levels = []
	
	
	if doc.doctype == "Stock Entry":
		for item in doc.items:
			if item.s_warehouse:
				curr_state_s = get_current_qty(item.item_code,item.s_warehouse)		
				inventory_levels.append(curr_state_s[0])
				# frappe.msgprint(str(curr_state_s))
			if item.t_warehouse:
				curr_state_t = get_current_qty(item.item_code,item.t_warehouse)		
				inventory_levels.append(curr_state_t[0])
				# frappe.msgprint(str(curr_state_t))
	else:
		for item in doc.items:
			curr_state = get_current_qty(item.item_code,item.warehouse)		
			inventory_levels.append(curr_state[0])

	
	return inventory_levels


def get_current_qty(item,warehouse):
	# frappe.throw(str(warehouse))
	
	data = frappe.db.sql(
		f"""
			SELECT ei.name as ecom_item, bin.item_code as item_code, integration_item_code, variant_id, actual_qty, warehouse, reserved_qty
			FROM `tabEcommerce Item` ei
				JOIN tabBin bin
				ON ei.erpnext_item_code = bin.item_code
			WHERE 
				bin.item_code = %(item)s
				and bin.warehouse = %(warehouse)s
		""",
		({
			"item":item,
			"warehouse":warehouse
		}),
		as_dict=1,
	)
	return data
@temp_shopify_session
def upload_inventory_data_to_shopify(inventory_levels, warehous_map) -> None:
	synced_on = now()
	# frappe.throw(str(inventory_levels))
	for d in inventory_levels:
		# frappe.throw(str(d))
		# frappe.throw(str(warehous_map))
		d.shopify_location_id = warehous_map.get(d.warehouse)
		try:
			
			variant = Variant.find(d.variant_id)
			inventory_id = variant.inventory_item_id

			InventoryLevel.set(
				location_id=d.shopify_location_id,
				inventory_item_id=inventory_id,
				# shopify doesn't support fractional quantity
				available=cint(d.actual_qty) - cint(d.reserved_qty),
			)
			update_inventory_sync_status(d.ecom_item, time=synced_on)
			d.status = "Success"
		except ResourceNotFound:
			# Variant or location is deleted, mark as last synced and ignore.
			update_inventory_sync_status(d.ecom_item, time=synced_on)
			d.status = "Not Found"
		except Exception as e:
			d.status = "Failed"
			d.failure_reason = str(e)

		frappe.db.commit()