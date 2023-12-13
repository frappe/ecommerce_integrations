import frappe
from frappe.utils import now
from frappe import _dict
from typing import List, Tuple
from ecommerce_integrations.shopify.connection import temp_shopify_session
from frappe.utils import cint, create_batch, now
from shopify.resources import InventoryLevel, Variant
import shopify
from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	update_inventory_sync_status,update_tags_sync_status
)
from pyactiveresource.connection import ResourceNotFound

from ecommerce_integrations.shopify.theme_template import update_product_tag

from ecommerce_integrations.shopify.inventory import _log_inventory_update_status
import requests


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
	
	update_tags(inventory_levels)

@frappe.whitelist()
def update_image_and_handel_erpnext_item():
	try:
		ecommerce_items = frappe.db.sql("""
		select erpnext_item_code,integration_item_code from `tabEcommerce Item` where integration = 'shopify' and image_handel_sync = 0 limit 50
		""",as_dict=1)
		frappe.msgprint(str(ecommerce_items))
		item_ids = [item.integration_item_code for item in ecommerce_items]
		shopify_settings = frappe.get_single("Shopify Setting")
		secret = shopify_settings.get_password("password")
		shopify_url = shopify_settings.shopify_url
		ids = ",".join(str(item) for item in item_ids)
		url = "{url}/admin/api/2023-07/products.json?fields=image,handle,id&&ids={ids}".format(url=shopify_url,ids=ids)
		ids = ",".join(str(item) for item in item_ids)
		
		headers = {
			"X-Shopify-Access-Token":secret
        }
		res= requests.get(url=url,headers=headers)
		if res.status_code == 200:
			res = res.json()
			for item in res['products']:
				frappe.db.set_value("Ecommerce Item", {"integration_item_code": item['id']},
				{"shopify_image_url": item['image']['src'], "product_handle": item['handle']})
				frappe.msgprint("Image and Handel updated for item {}".format(item['id']))				
	except Exception as e:
		frappe.log_error(title="Shopify Error", message=e)
		return None

	
	


	# Process the response data
	# ...
	

		

def update_tags(inventory_levels):
	settings = frappe.get_doc("Shopify Setting")
	erpnext_warehouse_list = settings.get_erpnext_warehouses()
	synced_on = now()
	for inventory_sync_batch in create_batch(inventory_levels, 50):
		for item in inventory_sync_batch:
			try:
				if item["actual_qty"] == 0:						
					stock_from_other_warehouses = frappe.db.sql(
							"""
							SELECT sum(actual_qty) as total_qty
							FROM `tabBin`
							WHERE
								item_code = %(item)s and warehouse in %(warehouses)s
							GROUP BY item_code
							""",
							{
								"item": item['item_code'],
								"warehouses": erpnext_warehouse_list,
							},
							as_dict=1,
						)
							
					if len(stock_from_other_warehouses) > 0 and stock_from_other_warehouses[0]['total_qty'] == 0.0:
						update_product_tag(item['item_code'],0)
						update_tags_sync_status(item['item_code'], time=synced_on)
						item.status = "Success"
					
						# frappe.enqueue('ecommerce_integrations.shopify.theme_template.update_product_tag',product_id=item['item_code'],available=0)
				else:
					update_product_tag(item['item_code'],1)
					update_tags_sync_status(item['item_code'], time=synced_on)
					item.status = "Success"
			except ResourceNotFound:
				# Variant or location is deleted, mark as last synced and ignore.
				update_tags_sync_status(item['item_code'], time=synced_on)
				item.status = "Not Found"
			except Exception as e:
				item.status = "Failed"
				item.failure_reason = str(e)
			
				# frappe.enqueue('ecommerce_integrations.shopify.theme_template.update_product_tag',product_id=item['item_code'],available=1)
		_log_inventory_update_status(inventory_sync_batch)
	

def get_doc_items_level(doc):
	inventory_levels = []
	for item in doc.items:
		if is_enabled_brand_item(item.item_code):
			
			if doc.doctype == "Stock Entry":
			
				if item.s_warehouse:
				
					current_stock_s = get_current_qty(item.item_code,item.s_warehouse)
					if current_stock_s:
					
						curr_state_s = current_stock_s	
						inventory_levels.append(curr_state_s[0])
				
				if item.t_warehouse:
					
					current_stock_t = get_current_qty(item.item_code,item.t_warehouse)
					
					if current_stock_t:
						curr_state_t = current_stock_t	
						inventory_levels.append(curr_state_t[0])
			else:
				
				current_stock_f = get_current_qty(item.item_code,item.warehouse)
				
				if current_stock_f:
					curr_state = current_stock_f	
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
				ei.erpnext_item_code = %(item)s
				and bin.warehouse = %(warehouse)s
		""",
		({
			"item":item,
			"warehouse":warehouse
		}),
		as_dict=1,
	)

	if len(data) > 0:
		return data
	
	return 0

@temp_shopify_session
def upload_inventory_data_to_shopify(inventory_levels, warehous_map) -> None:
	synced_on = now()
	for d in inventory_levels:

		if is_enabled_brand_item(d.item_code):
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
				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Not Found"
			except Exception as e:
				d.status = "Failed"
				d.failure_reason = str(e)

			frappe.db.commit()

@temp_shopify_session
def get_article_details(item):
	return shopify.Article.find(item)


def is_enabled_brand_item(product_id):
	shopify_settings = frappe.get_single("Shopify Setting")
	enabled_brand_list  = [item.brand for item in shopify_settings.enabled_brand]
 
	product_brand = frappe.db.get_value("Item",product_id,"brand")

	if product_brand in enabled_brand_list:
		return True
	else:
		return False

@frappe.whitelist()
def update_stock_on_click():
	"""
	Get list of dict containing items for which the inventory needs to be updated on Integeration.

	New inventory levels are identified by checking Bin modification timestamp,
	so ensure that if you sync the inventory with integration, you have also
	updated `inventory_synced_on` field in related Ecommerce Item.

	returns: list of _dict containing ecom_item, item_code, integration_item_code, variant_id, actual_qty, warehouse, reserved_qty
	"""

	setting = frappe.get_doc("Shopify Setting")

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		print("not enabled")
		return
	
	warehous_map = setting.get_erpnext_to_integration_wh_mapping()

	inventory_levels = get_inventory_levels_for_enabled_items(tuple(warehous_map.keys()))	
	
	frappe.msgprint("Found {} items for which invetory needs to sync on shopify! ".format(str(len(inventory_levels))))

	frappe.enqueue('ecommerce_integrations.shopify.inventory.upload_inventory_data_to_shopify',inventory_levels=inventory_levels,warehous_map=warehous_map)

	frappe.msgprint("Bulk stock sync is initialized and added in Queue!")


@frappe.whitelist()
def update_tags_on_click():

	setting = frappe.get_doc("Shopify Setting")

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		print("not enabled")
		return
	
	warehous_map = setting.get_erpnext_to_integration_wh_mapping()

	inventory_levels = get_inventory_levels_for_enabled_items(tuple(warehous_map.keys()), for_tags=1)

	frappe.msgprint("Found {} items for which tags needs to sync on shopify! ".format(str(len(inventory_levels))))

	frappe.enqueue('ecommerce_integrations.shopify.real_time_update.update_tags',inventory_levels=inventory_levels)

	frappe.msgprint("Bulk tags sync is initialized and added in Queue!")
	
	
def get_inventory_levels_for_enabled_items(warehouses,for_tags=False):
	"""
	Get list of dict containing items for which the inventory needs to be updated on Integeration.

	Fetch only those item which is enabled from the module

	returns: list of _dict containing ecom_item, item_code, integration_item_code, variant_id, actual_qty, warehouse, reserved_qty
	"""

	shopify_settings = frappe.get_single("Shopify Setting")

	enabled_brand_list  = [item.brand for item in shopify_settings.enabled_brand]

	data = get_data(warehouses, enabled_brand_list,for_tags)

	return data


def get_data(warehouses, brands,for_tags):
	warehouse_placeholders = ', '.join('%s' for _ in warehouses)
	brand_placeholders = ', '.join('%s' for _ in brands)



	# Create a tuple that contains all the values to be substituted into the query
	values_tuple = tuple(warehouses) + tuple(brands)

	if for_tags:
	
		
		sync_condition =  "(bin.modified > ei.tags_sync_on or ei.tags_sync_on is null)"
	else:
		
		sync_condition =  "(bin.modified > ei.inventory_synced_on or ei.inventory_synced_on is null)"
		
	
	sql_query = f"""
		SELECT ei.name as ecom_item, 
		bin.item_code as item_code, 
		integration_item_code, 
		variant_id, 
		actual_qty, 
		warehouse, 
		reserved_qty
		FROM `tabEcommerce Item` ei
		JOIN tabBin bin ON ei.erpnext_item_code = bin.item_code
		JOIN tabItem it ON ei.erpnext_item_code = it.name
		WHERE bin.warehouse IN ({warehouse_placeholders})
		  AND ei.integration = 'shopify'
		  AND it.brand IN ({brand_placeholders})
		  AND {sync_condition}
		"""
	# Execute the query with the frappe.db.sql function
	data = frappe.db.sql(
		sql_query,
		values=values_tuple,
		as_dict=1
	)

	

	return data