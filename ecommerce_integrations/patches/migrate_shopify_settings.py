import frappe

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.utils import normalize_shop_domain


def execute():
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	if not frappe.db.table_exists(f"tab{SETTING_DOCTYPE}"):
		return

	# Skip if settings already migrated.
	if frappe.db.count(SETTING_DOCTYPE):
		return

	if not frappe.db.table_exists("tabSingles"):
		return

	singleton_values = frappe.db.sql(
		"select field, value from tabSingles where doctype = %s", (SETTING_DOCTYPE,), as_list=True
	)

	if not singleton_values:
		return

	data = frappe._dict(singleton_values)
	shop_domain = normalize_shop_domain(data.get("shopify_url")) or "shopify-setting"
	data["shopify_url"] = shop_domain

	setting_doc = frappe.get_doc({"doctype": SETTING_DOCTYPE})
	setting_doc.set(data)
	setting_doc.flags.ignore_mandatory = True
	setting_doc.insert(ignore_permissions=True)

	_update_child_tables_parent(setting_doc.name)


def _update_child_tables_parent(new_parent: str) -> None:
	child_tables = [
		"Shopify Warehouse Mapping",
		"Shopify Tax Account",
		"Shopify Webhooks",
	]

	for child_table in child_tables:
		tablename = f"tab{child_table}"
		if not frappe.db.table_exists(tablename):
			continue

		frappe.db.sql(
			f"""UPDATE `{tablename}`
			SET parent = %s
			WHERE parent = %s AND parenttype = %s""",
			(new_parent, SETTING_DOCTYPE, SETTING_DOCTYPE),
		)
