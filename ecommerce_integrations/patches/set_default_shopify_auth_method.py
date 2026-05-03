import frappe

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE


def execute():
	"""Set default authentication method to 'Static Token' on existing Shopify Account rows.

	Ensures backward compatibility for installations that pre-date OAuth 2.0 support.
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_account")

	if not frappe.db.exists("DocType", ACCOUNT_DOCTYPE):
		return

	rows = frappe.get_all(
		ACCOUNT_DOCTYPE,
		filters={"authentication_method": ("in", ("", None))},
		pluck="name",
	)

	for name in rows:
		frappe.db.set_value(
			ACCOUNT_DOCTYPE,
			name,
			"authentication_method",
			"Static Token",
			update_modified=False,
		)

	if rows:
		frappe.db.commit()
		frappe.logger().info(
			f"Shopify Account: defaulted authentication_method to 'Static Token' for {len(rows)} row(s)"
		)
