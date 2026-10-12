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

	items_by_code = defaultdict(list)
	for item in doc.get("items"):
		items_by_code[item.item_code].append(item)

	rows = []
	for tax in doc.get("taxes"):
		for item_code, (rate, amount) in (tax.get(ITEM_WISE_TAX_KEY) or {}).items():
			# Unicommerce splits a qty>1 line into one row per unit; spread the tax across them.
			items = items_by_code.get(item_code) or []
			if not items:
				continue
			for item, share in zip(items, _split_amount(flt(amount), len(items)), strict=True):
				rows.append(
					frappe._dict(
						item=item,
						tax=tax,
						rate=flt(rate),
						amount=share,
						taxable_amount=flt(item.get("net_amount")) or flt(item.qty) * flt(item.rate),
					)
				)

	if rows:
		doc._item_wise_tax_details = rows


def _split_amount(amount, count):
	if count <= 1:
		return [amount]

	share = flt(amount / count, 2)
	return [share] * (count - 1) + [flt(amount - share * (count - 1), 2)]


def copy_item_wise_tax_details(target, source_name, source_doctype="Sales Order"):
	"""Rebuild item-wise tax on a doc mapped from another transaction (SI/DN/credit note); mappers drop it."""

	if not target.meta.get_field("item_wise_tax_details"):
		return

	source = frappe.get_doc(source_doctype, source_name)

	# the target item mapped from each source row, via the link field its mapper set
	item_by_source_row = {}
	for item in target.get("items"):
		source_row = item.get("sales_invoice_item") or item.get("si_detail") or item.get("so_detail")
		if source_row:
			item_by_source_row[source_row] = item

	# one entry per source row: an item billed on several rows keeps each row's tax
	breakup_by_tax_row = defaultdict(list)
	for row in source.get("item_wise_tax_details") or []:
		item = item_by_source_row.get(row.item_row)
		if item:
			breakup_by_tax_row[row.tax_row].append((item, row))

	if not breakup_by_tax_row:
		return

	# queue each account's source rows in order; the k-th target row of an
	# account pairs with the k-th source row, so a source row with no items
	# keeps its place in line
	source_tax_rows = {row.tax_row for row in source.get("item_wise_tax_details") or []}
	source_details = defaultdict(deque)
	for tax in source.taxes:
		if tax.name in source_tax_rows:
			source_details[tax.account_head].append(breakup_by_tax_row.get(tax.name, []))

	rows = []
	for tax in target.get("taxes"):
		queue = source_details.get(tax.account_head)
		if not queue:
			continue
		for item, row in queue.popleft():
			rows.append(
				frappe._dict(
					item=item,
					tax=tax,
					rate=row.rate,
					amount=row.amount,
					taxable_amount=row.taxable_amount,
				)
			)

	if rows:
		target._item_wise_tax_details = rows


def validate_tax_template(doc, method=None):
	"""Prevent users from using dummy tax category for any item tax templates"""
	item = doc

	for d in item.get("taxes", []):
		if d.get("tax_category") == DUMMY_TAX_CATEGORY:
			frappe.throw(
				_("Tax category: '{}' can not be used in any tax templates.").format(DUMMY_TAX_CATEGORY)
			)
