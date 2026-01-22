import frappe
from erpnext.setup.utils import enable_all_roles_and_domains
from frappe.utils import now_datetime


def before_tests():
	"""Global test setup - runs once before all tests"""
	frappe.clear_cache()

	# Create warehouse types first (needed for company setup)
	create_warehouse_types()
	frappe.db.commit()

	# Create basic company setup if missing
	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	year = now_datetime().year
	if not frappe.get_list("Company"):
		setup_complete(
			{
				"currency": "INR",
				"full_name": "Test User",
				"company_name": "Wind Power LLC",
				"timezone": "Asia/Kolkata",
				"company_abbr": "WP",
				"industry": "Manufacturing",
				"country": "India",
				"fy_start_date": f"{year}-01-01",
				"fy_end_date": f"{year}-12-31",
				"language": "english",
				"company_tagline": "Testing",
				"email": "test@erpnext.com",
				"password": "test",
				"chart_of_accounts": "Standard",
				"domains": ["Manufacturing"],
			}
		)

	# Global settings
	frappe.db.set_value("Stock Settings", None, "auto_insert_price_list_rate_if_missing", 0)
	enable_all_roles_and_domains()
	frappe.db.commit()

	# Create accounts needed by Unicommerce and other tests
	create_accounts_for_wind_power_llc()
	frappe.db.commit()


def create_warehouse_types():
	"""Create warehouse types - needed before companies are created"""
	warehouse_types = ["Transit", "Regular"]

	for wh_type in warehouse_types:
		if not frappe.db.exists("Warehouse Type", wh_type):
			try:
				frappe.get_doc(
					{
						"doctype": "Warehouse Type",
						"name": wh_type,
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass


def create_accounts_for_wind_power_llc():
	"""Create accounts needed by Unicommerce and other modules for Wind Power LLC"""
	company = "Wind Power LLC"

	# Accounts that tests expect to exist
	accounts_to_create = [
		{
			"account_name": "Output Tax GST",
			"account_type": "Tax",
			"parent_name": "Duties and Taxes - WP",
		},
		{
			"account_name": "Freight and Forwarding Charges",
			"account_type": "Expense Account",
			"parent_name": "Cost of Goods Sold - WP",
		},
		{
			"account_name": "Cash",
			"account_type": "Cash",
			"parent_name": "Cash In Hand - WP",
		},
		{
			"account_name": "Miscellaneous Expenses",
			"account_type": "Expense Account",
			"parent_name": "Indirect Expenses - WP",
		},
	]

	for account_data in accounts_to_create:
		account_name = f"{account_data['account_name']} - WP"

		if not frappe.db.exists("Account", account_name):
			try:
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": account_data["account_name"],
						"account_type": account_data["account_type"],
						"company": company,
						"parent_account": account_data["parent_name"],
						"is_group": 0,
						"report_type": "Balance Sheet",
					}
				).insert(ignore_if_duplicate=True)
			except Exception:
				pass
