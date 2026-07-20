import frappe

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE


def execute():
	"""
	Migration patch to set default authentication method for existing Shopify installations.
	This ensures backward compatibility when introducing OAuth 2.0 support.
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	if frappe.db.exists("DocType", SETTING_DOCTYPE):
		settings = frappe.get_doc(SETTING_DOCTYPE)

		# Set default authentication method to "Static Token" for existing installations
		if not settings.authentication_method:
			settings.db_set("authentication_method", "Static Token", update_modified=False)
			frappe.db.commit()

			frappe.logger().info(
				"Shopify Setting: Set default authentication method to 'Static Token' for existing installation"
			)
