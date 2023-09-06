import requests
from requests.exceptions import RequestException
import frappe



def update_item_theme_template(product_id,enquiry=0):
    
    shoppify_id = frappe.db.get_value("Ecommerce Item",{"erpnext_item_code":product_id},"integration_item_code")
    if shoppify_id:
        if is_ecommerce_item(product_id):
            shopify_settings = frappe.get_single("Shopify Setting")
            url = "https://"+shopify_settings.shopify_url+"/admin/api/2023-07/products/{product_id}.json".format(product_id=shoppify_id)
            secret = shopify_settings.get_password("password")
            if enquiry:
                template_name = shopify_settings.enquiry_template_name
            else:
                template_name = "Default product"
            data = {
                "product":{
                    "id":shoppify_id,
                    "template_suffix":template_name
                }
            }
            
            headers = {
                "X-Shopify-Access-Token":secret
            }
            res = post_request(url,data,headers)
 

def post_request(url, data, headers):
    try:
        response = requests.put(url, json=data, headers=headers)
        response.raise_for_status()

    except RequestException as err:
        frappe.log_error(title="Shoppify Product theme template update call",message=err)
        return None

    except Exception as err:
        frappe.log_error(title="Shoppify Product theme template update call",message=err)
        return None

    return response.json()

def get_request(url, headers):
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

    except RequestException as err:
        frappe.log_error(title="Shoppify Product get call",message=err)
        return None

    except Exception as err:
        frappe.log_error(title="Shoppify Product get call",message=err)
        return None

    return response.json()




def get_product_tag(product_id):
    shopify_settings = frappe.get_single("Shopify Setting")
    secret = shopify_settings.get_password("password")
    url = "https://"+shopify_settings.shopify_url+"/admin/api/2023-07/products/{product_id}.json".format(product_id=product_id)
    headers = {
        "X-Shopify-Access-Token":secret
    }
    res=get_request(url, headers)
    if res:
        tags = res["product"]['tags']
        tags_list = tags.split(",")
    else:
        tags_list = []
    return tags_list
    
def update_product_tag(product_id,available=0):
    # frappe.msgprint("in update tag")
    
    shoppify_id = frappe.db.get_value("Ecommerce Item",{"erpnext_item_code":product_id},"integration_item_code")
    if shoppify_id:
        shopify_settings = frappe.get_single("Shopify Setting")
        secret = shopify_settings.get_password("password")
        url = "https://"+shopify_settings.shopify_url+"/admin/api/2023-07/products/{product_id}.json".format(product_id=shoppify_id)
        headers = {
            "X-Shopify-Access-Token":secret
        }
        
        tags = get_product_tag(shoppify_id)
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

        # frappe.msgprint(str(tags))
        data = {
            "product":{
                "id":shoppify_id,
                "tags":tags
            }
        }
        res = post_request(url,data,headers)
        return res

def clean_list(lst):
    return [item.strip() for item in lst]

def is_ecommerce_item(product_id):
    shopify_settings = frappe.get_single("Shopify Setting")
    ecommerce_brand_list  = [item.brand for item in shopify_settings.ecommerce_item_group]
 
    product_brand = frappe.db.get_value("Item",product_id,"brand")

    if product_brand in ecommerce_brand_list:
        return True
    else:
        return False
    
def remove_element(lst, element):
    """Remove all occurrences of an element from the list."""
    while element in lst:
        lst.remove(element)
    return lst



    