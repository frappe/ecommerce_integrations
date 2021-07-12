import frappe
from frappe import _

DUMMY_TAX_CATEGORY = "Ecommerce Integrations - Ignore"


def get_dummy_tax_category() -> str:
	"""Get a dummy tax category used for ignoring tax templates.

	This is used for ensuring that no tax templates are applied on transaction."""

	if not frappe.db.exists("Tax Category", DUMMY_TAX_CATEGORY):
		frappe.get_doc(doctype="Tax Category", title=DUMMY_TAX_CATEGORY).insert()
	return DUMMY_TAX_CATEGORY


def validate_tax_template(doc, method=None):
	"""Prevent users from using dummy tax category for any item tax templates"""
	item = doc

	for d in item.get("taxes", []):
		if d.get("tax_category") == DUMMY_TAX_CATEGORY:
			frappe.throw(
				_("Tax category: '{}' can not be used in any tax templates.").format(DUMMY_TAX_CATEGORY)
			)
