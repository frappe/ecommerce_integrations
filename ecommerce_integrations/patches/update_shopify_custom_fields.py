import frappe

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE
from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import (
	setup_custom_fields,
)


def execute():
	# Runs before the Shopify Setting single is migrated on older sites, after it on newer ones.
	legacy_enabled = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'Shopify Setting' and field = 'enable_shopify'"
	)
	account_enabled = frappe.db.table_exists(ACCOUNT_DOCTYPE) and frappe.db.exists(
		ACCOUNT_DOCTYPE, {"enable_shopify": 1}
	)

	if (legacy_enabled and frappe.utils.cint(legacy_enabled[0][0])) or account_enabled:
		setup_custom_fields()
