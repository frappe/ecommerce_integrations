import frappe

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, AUTH_METHOD_STATIC


def execute():
	"""
	Migration patch: set default authentication method for existing Shopify installations.
	Ensures backward compatibility when introducing OAuth 2.0 support.

	Existing accounts get "Static Token" so their current setup continues working. It runs
	after the `Shopify Setting` single was migrated into a `Shopify Account`.
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_account")

	account = frappe.qb.DocType(ACCOUNT_DOCTYPE)
	(
		frappe.qb.update(account)
		.set(account.authentication_method, AUTH_METHOD_STATIC)
		.where(account.authentication_method.isnull() | (account.authentication_method == ""))
	).run()
