import frappe
from frappe import _

DUMMY_PRICE_LIST = "Ecommerce Integrations - Ignore"


def get_dummy_price_list() -> str:
	"""Get a dummy tax category used for ignoring tax templates.

	This is used for ensuring that no tax templates are applied on transaction."""

	if not frappe.db.exists("Price List", DUMMY_PRICE_LIST):
		pl = frappe.get_doc(doctype="Price List", price_list_name=DUMMY_PRICE_LIST, selling=1).insert()
		pl.add_comment(text=_("This price list is used by integrations and should be left empty"))
	return DUMMY_PRICE_LIST


def discard_item_prices(doc, method=None):
	"""Discard any item prices added in dummy price list"""
	if doc.price_list == DUMMY_PRICE_LIST:
		frappe.enqueue(method=_delete_all_dummy_prices, queue="short", enqueue_after_commit=True)


def _delete_all_dummy_prices():
	frappe.db.delete("Item Price", {"price_list": DUMMY_PRICE_LIST, "selling": 1})
