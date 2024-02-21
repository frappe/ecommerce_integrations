import frappe
from frappe.utils import now
from frappe import _dict
from typing import List, Tuple
from ecommerce_integrations.shopify.connection import temp_shopify_session
from frappe.utils import cint, now, create_batch
from shopify.resources import InventoryLevel, Variant, Product
import shopify
from ecommerce_integrations.controllers.inventory import (
	update_inventory_sync_status,
)
from pyactiveresource.connection import ResourceNotFound
from ecommerce_integrations.shopify.inventory import _log_inventory_update_status





def update_inventory_on_shopify_real_time(doc):
	"""Upload stock levels from ERPNext to Shopify.

	Called by scheduler on configured interval.
	"""
	
	setting = frappe.get_doc("Shopify Setting")

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		
		return
	
	warehous_map = setting.get_erpnext_to_integration_wh_mapping()

	inventory_levels = get_doc_items_level(doc)
 
	upload_inventory_data_to_shopify(inventory_levels, warehous_map)	

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
	for inventory_sync_batch in create_batch(inventory_levels, 50):
		for d in inventory_sync_batch:

			if is_enabled_brand_item(d.item_code):
				d.shopify_location_id = warehous_map.get(d.warehouse)
				try:
					
					variant = Variant.find(d.variant_id)

					product = Product.find(d.integration_item_code)
		
					inventory_id = variant.inventory_item_id

					InventoryLevel.set(
						location_id=d.shopify_location_id,
						inventory_item_id=inventory_id,
						# shopify doesn't support fractional quantity
						available=cint(d.actual_qty) - cint(d.reserved_qty),
					)
		
					
					tags = product.tags.split(', ')
		
					if check_overall_availability(d.item_code):
						modified_tags = modify_tag(tags,d.item_code,available=1)
					else:
						modified_tags = modify_tag(tags,d.item_code,available=0)
					
					product.tags = ', '.join(modified_tags)
					product.save()
					frappe.log_error(title="shopify sync debug",message="syncing for {} and updating tags as {}".format(d,str(modified_tags)))
					update_inventory_sync_status(d.ecom_item, time=synced_on)
					d.status = "Success"
				except ResourceNotFound:
					update_inventory_sync_status(d.ecom_item, time=synced_on)
					d.status = "Not Found"
				except Exception as e:
					d.status = "Failed"
					d.failure_reason = str(e)

				frappe.db.commit()
    
		_log_inventory_update_status(inventory_sync_batch)

def modify_tag(tags,product_id,available=0):
	
	tags = clean_list(tags)
	
	not_available_tag = "Not Available"
	enuiry_tag = "enquire-product"

	if available and is_ecommerce_item(product_id): 
		
		if not_available_tag in tags:
			remove_element(tags, not_available_tag)
					
		if "Available" not in tags:                
			tags.append("Available")
		
		if "Available Online" not in tags:                
			tags.append("Available Online")
			
		if enuiry_tag in tags:                
			remove_element(tags,enuiry_tag)

	elif available:
		         
		if not_available_tag in tags:
			remove_element(tags, not_available_tag)

		if "Available Online" in tags:
			remove_element(tags, "Available Online")
					
		if "Available" not in tags:                
			tags.append("Available")

	else:
		  
		if "Available" in tags:
			remove_element(tags, "Available")

		if "Available Online" in tags:               
			remove_element(tags, "Available Online")      
			
		if not_available_tag not in tags:                
			tags.append(not_available_tag)

		if is_ecommerce_item(product_id) and enuiry_tag not in tags:                
			tags.append(enuiry_tag)
   
	return tags
    
def is_ecommerce_item(product_id):
    shopify_settings = frappe.get_single("Shopify Setting")
    ecommerce_brand_list  = [item.brand for item in shopify_settings.ecommerce_item_group]
 
    product_brand = frappe.db.get_value("Item",product_id,"brand")

    if product_brand in ecommerce_brand_list:
        return True
    else:
        return False
    
def check_overall_availability(item):
	settings = frappe.get_doc("Shopify Setting")
	erpnext_warehouse_list = settings.get_erpnext_warehouses()
	stock_from_other_warehouses = frappe.db.sql(
			"""
			SELECT sum(actual_qty) as total_qty
			FROM `tabBin`
			WHERE
				item_code = %(item)s and warehouse in %(warehouses)s
			GROUP BY item_code
			""",
			{
				"item": item,
				"warehouses": erpnext_warehouse_list,
			},
			as_dict=1,
		)
	
	if len(stock_from_other_warehouses) > 0:
		if stock_from_other_warehouses[0]['total_qty'] == 0.0:
			
			return 0
		else:
			return 1
	else:
		return 0
  
def remove_element(lst, element):
    """Remove all occurrences of an element from the list."""
    while element in lst:
        lst.remove(element)
    return lst	

def clean_list(lst):
    return [item.strip() for item in lst]
    
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

	frappe.enqueue('ecommerce_integrations.shopify.real_time_update.upload_inventory_data_to_shopify',inventory_levels=inventory_levels,warehous_map=warehous_map)
	

	frappe.msgprint("Bulk stock sync is initialized and added in Queue!")

	
	
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

@frappe.whitelist()	
def set_main_image_and_handle_in_erpnext(shopify_id):
	import requests
	shopify_settings = frappe.get_single("Shopify Setting")
	secret = shopify_settings.get_password("password")
	shopify_url = shopify_settings.shopify_url
	url = "https://{url}/admin/api/2023-07/products/{id}.json?fields=image,handle".format(url=shopify_url,id=shopify_id)
	headers = {
		"X-Shopify-Access-Token":secret
    }
	try:
		res= requests.get(url=url,headers=headers)
		if res.status_code == 200:
			res = res.json()
			if res['product']['image']:
				erpnext_item_code = frappe.db.get_value("Ecommerce Item", {"integration_item_code": shopify_id},"erpnext_item_code")
				frappe.db.set_value("Item",erpnext_item_code,{"image":res['product']['image']['src'],"product_handle":res['product']['handle']})
				frappe.db.set_value("Ecommerce Item",{"integration_item_code": shopify_id},"image_handel_sync",1)
				frappe.db.commit()
				frappe.msgprint("Image updated for item {}".format(shopify_id))
			else:
				frappe.msgprint("Image not found for item {}".format(shopify_id))
	except Exception as e:
		frappe.log_error(title="Shopify Image Sync Error", message=e)
  
@frappe.whitelist()
def bulk_update_item_handle_and_image():
	shopify_items = frappe.get_all("Ecommerce Item",filters={"integration":"shopify","image_handel_sync":0},fields=["integration_item_code"])
	for item in shopify_items:
		frappe.enqueue('ecommerce_integrations.shopify.real_time_update.set_main_image_and_handle_in_erpnext',shopify_id=item.integration_item_code)