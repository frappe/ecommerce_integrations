# Copyright (c) 2021, Frappe and Contributors
# See license.txt

import unittest
import json
import frappe

from ecommerce_integrations.shopify.product import ShopifyProduct

class TestProduct(unittest.TestCase):

	@classmethod
	def setUpClass(cls):
		shopify_item_code = []
		for d in frappe.get_list("Ecommerce Item"):
			doc = frappe.get_doc("Ecommerce Item", d.name)
			shopify_item_code.append(doc.erpnext_item_code)
			doc.delete()

		for name in shopify_item_code:
			frappe.get_doc("Item", name).delete()

	def test_sync_single_product(self):
		product_dict = json.loads(single_product_json)

		product = ShopifyProduct(product_dict["id"])
		product._make_item(product_dict)   # to use JSON


	def test_sync_product_with_variants(self):
		product_dict = json.loads(product_with_variants_json)

		product = ShopifyProduct(product_dict["id"])
		product._make_item(product_dict)


single_product_json = """
{
	"id": 6675003310233,
	"title": "A book",
	"body_html": "You read this thing",
	"vendor": "Amazin",
	"product_type": "Book",
	"created_at": "2021-04-08T10:05:56+05:30",
	"handle": "a-book",
	"updated_at": "2021-04-19T19:56:25+05:30",
	"published_at": "2021-04-08T13:47:09+05:30",
	"template_suffix": "",
	"status": "active",
	"published_scope": "web",
	"tags": "book, read",
	"admin_graphql_api_id": "gid://shopify/Product/6675003310233",
	"variants": [
		{
			"id": 39733070200985,
			"title": "Default Title",
			"price": "100.00",
			"sku": "BOOK-0001",
			"position": 1,
			"inventory_policy": "deny",
			"compare_at_price": "10.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "Default Title",
			"option2": null,
			"option3": null,
			"created_at": "2021-04-08T10:05:56+05:30",
			"updated_at": "2021-04-19T19:56:25+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41827387244697,
			"inventory_quantity": 72,
			"old_inventory_quantity": 72,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39733070200985"
		}
	],
	"options": [
		{
			"id": 8561991975065,
			"product_id": 6675003310233,
			"name": "Title",
			"position": 1,
			"values": ["Default Title"]
		}
	],
	"images": [
		{
			"id": 29008982540441,
			"position": 1,
			"created_at": "2021-04-08T10:05:58+05:30",
			"updated_at": "2021-04-08T10:05:58+05:30",
			"alt": null,
			"width": 2000,
			"height": 1333,
			"src": "https://cdn.shopify.com/s/files/1/0557/8804/4441/products/book.jpg?v=1617856558",
			"variant_ids": [],
			"admin_graphql_api_id": "gid://shopify/ProductImage/29008982540441"
		}
	],
	"image": {
		"id": 29008982540441,
		"position": 1,
		"created_at": "2021-04-08T10:05:58+05:30",
		"updated_at": "2021-04-08T10:05:58+05:30",
		"alt": null,
		"width": 2000,
		"height": 1333,
		"src": "https://cdn.shopify.com/s/files/1/0557/8804/4441/products/book.jpg?v=1617856558",
		"variant_ids": [],
		"admin_graphql_api_id": "gid://shopify/ProductImage/29008982540441"
	}
}
"""



product_with_variants_json = """
{
	"id": 6704435495065,
	"title": "short t shirt",
	"body_html": "THis is a t shirt.",
	"vendor": "frappetest",
	"product_type": "shirt",
	"created_at": "2021-04-20T16:39:49+05:30",
	"handle": "short-t-shirt",
	"updated_at": "2021-04-20T16:39:52+05:30",
	"published_at": "2021-04-20T16:39:51+05:30",
	"template_suffix": "",
	"status": "active",
	"published_scope": "web",
	"tags": "",
	"admin_graphql_api_id": "gid://shopify/Product/6704435495065",
	"variants": [
		{
			"id": 39845261443225,
			"title": "S / Red",
			"price": "1000.00",
			"sku": "TSHIRT-001",
			"position": 1,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "S",
			"option2": "Red",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939613843609,
			"inventory_quantity": 100,
			"old_inventory_quantity": 100,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261443225"
		},
		{
			"id": 39845261475993,
			"title": "S / Blue",
			"price": "1000.00",
			"sku": "TSHIRT-002",
			"position": 2,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "S",
			"option2": "Blue",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939613876377,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261475993"
		},
		{
			"id": 39845261508761,
			"title": "S / Green",
			"price": "1000.00",
			"sku": "TSHIRT-003",
			"position": 3,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "S",
			"option2": "Green",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939613909145,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261508761"
		},
		{
			"id": 39845261541529,
			"title": "M / Red",
			"price": "1000.00",
			"sku": "TSHIRT-004",
			"position": 4,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "M",
			"option2": "Red",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939613941913,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261541529"
		},
		{
			"id": 39845261574297,
			"title": "M / Blue",
			"price": "1000.00",
			"sku": "TSHIRT-005",
			"position": 5,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "M",
			"option2": "Blue",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939613974681,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261574297"
		},
		{
			"id": 39845261607065,
			"title": "M / Green",
			"price": "1000.00",
			"sku": "TSHIRT-006",
			"position": 6,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "M",
			"option2": "Green",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939614007449,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261607065"
		},
		{
			"id": 39845261639833,
			"title": "L / Red",
			"price": "1000.00",
			"sku": "TSHIRT-007",
			"position": 7,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "L",
			"option2": "Red",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939614040217,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261639833"
		},
		{
			"id": 39845261672601,
			"title": "L / Blue",
			"price": "1000.00",
			"sku": "TSHIRT-008",
			"position": 8,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "L",
			"option2": "Blue",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939614072985,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261672601"
		},
		{
			"id": 39845261705369,
			"title": "L / Green",
			"price": "1000.00",
			"sku": "TSHIRT-009",
			"position": 9,
			"inventory_policy": "deny",
			"compare_at_price": "55.00",
			"fulfillment_service": "manual",
			"inventory_management": "shopify",
			"option1": "L",
			"option2": "Green",
			"option3": null,
			"created_at": "2021-04-20T16:39:49+05:30",
			"updated_at": "2021-04-20T16:39:49+05:30",
			"taxable": true,
			"barcode": "",
			"grams": 0,
			"image_id": null,
			"weight": 0.0,
			"weight_unit": "kg",
			"inventory_item_id": 41939614105753,
			"inventory_quantity": 0,
			"old_inventory_quantity": 0,
			"requires_shipping": true,
			"admin_graphql_api_id": "gid://shopify/ProductVariant/39845261705369"
		}
	],
	"options": [
		{
			"id": 8595677413529,
			"product_id": 6704435495065,
			"name": "Size",
			"position": 1,
			"values": ["S", "M", "L"]
		},
		{
			"id": 8595677446297,
			"product_id": 6704435495065,
			"name": "Color",
			"position": 2,
			"values": ["Red", "Blue", "Green"]
		}
	],
	"images": [
		{
			"id": 29582150795417,
			"position": 1,
			"created_at": "2021-04-20T16:39:51+05:30",
			"updated_at": "2021-04-20T16:39:51+05:30",
			"alt": null,
			"width": 554,
			"height": 248,
			"src": "https://cdn.shopify.com/s/files/1/0557/8804/4441/products/Screenshot2021-04-17at3.19.24PM.png?v=1618916991",
			"variant_ids": [],
			"admin_graphql_api_id": "gid://shopify/ProductImage/29582150795417"
		}
	],
	"image": {
		"id": 29582150795417,
		"position": 1,
		"created_at": "2021-04-20T16:39:51+05:30",
		"updated_at": "2021-04-20T16:39:51+05:30",
		"alt": null,
		"width": 554,
		"height": 248,
		"src": "https://cdn.shopify.com/s/files/1/0557/8804/4441/products/Screenshot2021-04-17at3.19.24PM.png?v=1618916991",
		"variant_ids": [],
		"admin_graphql_api_id": "gid://shopify/ProductImage/29582150795417"
	}
}
"""
