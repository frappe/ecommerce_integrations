"""Move the configuration of the `Shopify Setting` single into a `Shopify Account`.

The single held one store. Its values become the first account, named after the shop URL;
its child rows (tax accounts, warehouse mapping, registered webhooks) and its stored
password move along, so the store keeps working without registering its webhooks again.
Existing Ecommerce Items and Shopify integration logs belong to that store and are linked
to the account. Afterwards the single is removed.
"""

import frappe
from frappe.model import table_fields

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, MODULE_NAME

LEGACY_SETTING_DOCTYPE = "Shopify Setting"
CHILD_DOCTYPES = ("Shopify Tax Account", "Shopify Warehouse Mapping", "Shopify Webhooks")


def execute():
	frappe.reload_doc("shopify", "doctype", "shopify_account")
	frappe.reload_doc("ecommerce_integrations", "doctype", "ecommerce_item")
	frappe.reload_doc("ecommerce_integrations", "doctype", "ecommerce_integration_log")

	values = get_legacy_values()
	shopify_url = (values.get("shopify_url") or "").replace("https://", "").strip()

	if shopify_url and not frappe.db.exists(ACCOUNT_DOCTYPE, shopify_url):
		create_account(shopify_url, values)
		move_child_rows(shopify_url)
		move_passwords(shopify_url)
		link_existing_records(shopify_url)

	if frappe.db.exists("DocType", LEGACY_SETTING_DOCTYPE):
		frappe.delete_doc("DocType", LEGACY_SETTING_DOCTYPE, ignore_missing=True, force=True)
	# deleting the DocType leaves the values of a single behind
	frappe.db.delete("Singles", {"doctype": LEGACY_SETTING_DOCTYPE})


def get_legacy_values() -> dict:
	return dict(
		frappe.db.sql(
			"select field, value from `tabSingles` where doctype = %s",
			LEGACY_SETTING_DOCTYPE,
		)
	)


def create_account(shopify_url: str, values: dict) -> None:
	"""Insert the account row without running its controller.

	Validation would register the webhooks with Shopify again, and on_update would start the
	old-connector migration; both already happened for this store.
	"""
	account = frappe.new_doc(ACCOUNT_DOCTYPE)
	for df in account.meta.fields:
		if df.fieldtype in table_fields or df.fieldname not in values:
			continue
		account.set(df.fieldname, values[df.fieldname])

	account.name = shopify_url
	account.shopify_url = shopify_url
	account.db_insert()


def move_child_rows(shopify_url: str) -> None:
	for child_doctype in CHILD_DOCTYPES:
		frappe.db.sql(
			f"""update `tab{child_doctype}`
			set parent = %(account)s, parenttype = %(account_doctype)s
			where parenttype = %(legacy)s""",
			{"account": shopify_url, "account_doctype": ACCOUNT_DOCTYPE, "legacy": LEGACY_SETTING_DOCTYPE},
		)


def move_passwords(shopify_url: str) -> None:
	frappe.db.sql(
		"""update `__Auth`
		set doctype = %(account_doctype)s, name = %(account)s
		where doctype = %(legacy)s and name = %(legacy)s""",
		{"account": shopify_url, "account_doctype": ACCOUNT_DOCTYPE, "legacy": LEGACY_SETTING_DOCTYPE},
	)


def link_existing_records(shopify_url: str) -> None:
	for doctype in ("Ecommerce Item", "Ecommerce Integration Log"):
		frappe.db.sql(
			f"""update `tab{doctype}`
			set shopify_account = %(account)s
			where integration = %(integration)s and coalesce(shopify_account, '') = ''""",
			{"account": shopify_url, "integration": MODULE_NAME},
		)
