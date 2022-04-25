import frappe


def execute():
	if frappe.db.get_single_value("Amazon SP API Settings", "enable_amazon"):
		try:
			amz_settings = frappe.get_doc("Amazon SP API Settings")

			frappe.reload_doc("amazon", "doctype", frappe.scrub("Amazon SP API Settings"))

			new_amz_setting = frappe.new_doc("Amazon SP API Settings")
			new_amz_setting.is_active = amz_settings.enable_amazon
			new_amz_setting.iam_arn = amz_settings.iam_arn
			new_amz_setting.refresh_token = amz_settings.refresh_token
			new_amz_setting.client_id = amz_settings.client_id
			new_amz_setting.client_secret = amz_settings.get_password("client_secret")
			new_amz_setting.aws_access_key = amz_settings.aws_access_key
			new_amz_setting.aws_secret_key = amz_settings.get_password("aws_secret_key")
			new_amz_setting.country = amz_settings.country
			new_amz_setting.company = amz_settings.company
			new_amz_setting.warehouse = amz_settings.warehouse
			new_amz_setting.parent_item_group = amz_settings.parent_item_group
			new_amz_setting.price_list = amz_settings.price_list
			new_amz_setting.customer_group = amz_settings.customer_group
			new_amz_setting.territory = amz_settings.territory
			new_amz_setting.customer_type = amz_settings.customer_type
			new_amz_setting.market_place_account_group = amz_settings.market_place_account_group
			new_amz_setting.after_date = amz_settings.after_date
			new_amz_setting.taxes_charges = amz_settings.taxes_charges
			new_amz_setting.enable_sync = amz_settings.enable_sync
			new_amz_setting.max_retry_limit = amz_settings.max_retry_limit
			new_amz_setting.is_old_data_migrated = amz_settings.is_old_data_migrated

			new_amz_setting.insert()
		except Exception as e:
			frappe.log_error(
				message=e, title=f'Method "{execute.__name__}" failed',
			)
