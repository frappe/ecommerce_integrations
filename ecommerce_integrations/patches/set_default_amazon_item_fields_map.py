import frappe


def execute():
	frappe.reload_doc("amazon", "doctype", "amazon_sp_api_settings")

	default_fields_map = [
		{"amazon_field": "ASIN", "item_field": "item_code", "use_to_find_item_code": 1},
		{"amazon_field": "SellerSKU", "item_field": None, "use_to_find_item_code": 0,},
		{"amazon_field": "Title", "item_field": None, "use_to_find_item_code": 0,},
	]
	amz_settings = frappe.db.get_all("Amazon SP API Settings", pluck="name")

	if amz_settings:
		for amz_setting in amz_settings:
			amz_setting_doc = frappe.get_doc("Amazon SP API Settings", amz_setting)

			for field_map in default_fields_map:
				amz_setting_doc.append("amazon_fields_map", field_map)

			amz_setting.flags.ignore_validate = True
			amz_setting_doc.save(ignore_version=True)
