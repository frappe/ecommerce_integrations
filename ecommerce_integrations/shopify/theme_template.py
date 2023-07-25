import requests
from requests.exceptions import RequestException
import frappe



def update_item_theme_template(product_id,enquiry=0):
    shopify_settings = frappe.get_single("Shopify Setting")
    url = "https://"+shopify_settings.shopify_url+"/admin/api/2023-07/products/{product_id}.json".format(product_id=product_id)
    secret = shopify_settings.get_password("password")
    if enquiry:
        template_name = shopify_settings.enquiry_template_name
    else:
        template_name = "Default product"
    data = {
        "product":{
            "id":product_id,
            "template_suffix":template_name
        }
    }
    headers = {
        "X-Shopify-Access-Token":secret
    }
    res = post_request(url,data,headers)
    print(res)

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

def update_tag(product,stock_availibility=0):
    shopify_settings = frappe.get_single("Shopify Setting")


def get_product_tag(product_id):
    shopify_settings = frappe.get_single("Shopify Setting")
    secret = shopify_settings.get_password("password")
    url = "https://"+shopify_settings.shopify_url+"/admin/api/2023-07/products/{product_id}.json".format(product_id=product_id)
    headers = {
        "X-Shopify-Access-Token":secret
    }
    res=get_request(url, headers)
    tags = res["product"]['tags']
    tags_list = tags.split(",")
    return tags_list

def update_product_tag(product_id,available=0):
    shopify_settings = frappe.get_single("Shopify Setting")
    secret = shopify_settings.get_password("password")
    url = "https://"+shopify_settings.shopify_url+"/admin/api/2023-07/products/{product_id}.json".format(product_id=product_id)
    headers = {
        "X-Shopify-Access-Token":secret
    }
    
    tags = get_product_tag(product_id)
    available_tag = "Available Online" if is_ecommerce_item(product_id) else "Available"
    not_avaialble_tag = "Not Available"
    if available:
        if not_avaialble_tag in tags:
            tags.remove(not_avaialble_tag)
            # remove_tag(not_avaialble_tag,tags)
        if available_tag not in tags:
            tags.append(available_tag)
            # add_tag(available_tag,tags)
    else:
        if available_tag in tags:
            tags.remove(available_tag)
            # remove_tag(available_tag,tags)
        if not_avaialble_tag not in tags:
            tags.append(not_avaialble_tag)
            # add_tag(not_avaialble_tag,tags)
    data = {
        "product":{
            "id":product_id,
            "tags":tags
        }
    }
    res = post_request(url,data,headers)
    return res

def is_ecommerce_item(product_id):
    shopify_settings = frappe.get_single("Shopify Setting")
    ecommerce_brand_list  = [item.brand for item in shopify_settings.ecommerce_item_group]
    print(ecommerce_brand_list)
    product_brand = frappe.db.get_value("Item",product_id,"brand")
    print(product_brand)
    if product_brand in ecommerce_brand_list:
        return True
    else:
        return False



    