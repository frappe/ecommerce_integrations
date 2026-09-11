from collections import defaultdict, deque

import frappe
from frappe import _
from frappe.utils import flt

DUMMY_TAX_CATEGORY = "Ecommerce Integrations - Ignore"

# Internal per-tax-row carrier for {item_code: [rate, amount]}.
ITEM_WISE_TAX_KEY = "_item_wise_tax"


def get_dummy_tax_category() -> str:
	"""Get a dummy tax category used for ignoring tax templates.

	This is used for ensuring that no tax templates are applied on transaction."""

	if not frappe.db.exists("Tax Category", DUMMY_TAX_CATEGORY):
		frappe.get_doc(doctype="Tax Category", title=DUMMY_TAX_CATEGORY).insert()
	return DUMMY_TAX_CATEGORY


def set_item_wise_tax_details(doc):
	"""Seed `_item_wise_tax_details` for our dont_recompute rows (ERPNext v15+ won't build it)."""

	if not doc.meta.get_field("item_wise_tax_details"):
		return

	items_by_code = {}
	for item in doc.get("items"):
		items_by_code.setdefault(item.item_code, item)

	rows = []
	for tax in doc.get("taxes"):
		for item_code, (rate, amount) in (tax.get(ITEM_WISE_TAX_KEY) or {}).items():
			item = items_by_code.get(item_code)
			if not item:
				continue

			rows.append(
				frappe._dict(
					item=item,
					tax=tax,
					rate=flt(rate),
					amount=flt(amount),
					taxable_amount=flt(item.get("net_amount")) or flt(item.qty) * flt(item.rate),
				)
			)

	if rows:
		doc._item_wise_tax_details = rows


def copy_item_wise_tax_details(target, source_name):
	"""Rebuild item-wise tax on a doc mapped from a Sales Order (SI/DN); the SO->SI mapper drops it."""

	if not target.meta.get_field("item_wise_tax_details"):
		return

	source = frappe.get_doc("Sales Order", source_name)
	item_code_by_row = {row.name: row.item_code for row in source.items}

	# {source tax row -> {item_code: [rate, amount]}}, keeping each row's own breakup
	detail_by_tax_row = {}
	for row in source.get("item_wise_tax_details") or []:
		item_code = item_code_by_row.get(row.item_row)
		if item_code:
			detail_by_tax_row.setdefault(row.tax_row, {})[item_code] = [row.rate, row.amount]

	if not detail_by_tax_row:
		return

	# Source rows carrying a breakup, queued per account in order; the mapper preserves
	# order, so the k-th target row of an account maps to the k-th source row.
	source_details = defaultdict(deque)
	for tax in source.taxes:
		if tax.name in detail_by_tax_row:
			source_details[tax.account_head].append(detail_by_tax_row[tax.name])

	for tax in target.get("taxes"):
		queue = source_details.get(tax.account_head)
		if queue:
			tax.set(ITEM_WISE_TAX_KEY, queue.popleft())

	set_item_wise_tax_details(target)


def validate_tax_template(doc, method=None):
	"""Prevent users from using dummy tax category for any item tax templates"""
	item = doc

	for d in item.get("taxes", []):
		if d.get("tax_category") == DUMMY_TAX_CATEGORY:
			frappe.throw(
				_("Tax category: '{}' can not be used in any tax templates.").format(DUMMY_TAX_CATEGORY)
			)
