# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe


def company_currency(company):
	return frappe.get_cached_value("Company", company, "default_currency")


def order_currency(order, company):
	"""ERPNext Currency name for a Medusa order (Medusa uses lowercase ISO codes)."""
	code = (order.get("currency_code") or "").upper()
	return code or company_currency(company)


def exchange_rate(from_currency, to_currency, transaction_date):
	"""FX rate via ERPNext: Currency Exchange records first, then the configured provider."""
	if from_currency == to_currency:
		return 1.0
	from erpnext.setup.utils import get_exchange_rate

	return get_exchange_rate(from_currency, to_currency, transaction_date) or 1.0


def receivable_account(company, currency):
	"""Receivable account in ``currency`` under ``company``, auto-created if missing.

	ERPNext requires a Sales Invoice's ``debit_to`` account currency to equal the document
	currency, so each non-company currency gets a dedicated ``Debtors <CCY>`` account.
	"""
	default = frappe.get_cached_value("Company", company, "default_receivable_account")
	if currency == company_currency(company):
		return default

	existing = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Receivable", "account_currency": currency, "is_group": 0},
		"name",
	)
	if existing:
		return existing

	parent = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Receivable", "is_group": 1}, "name"
	)
	if not parent and default:
		parent = frappe.db.get_value("Account", default, "parent_account")

	account = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": f"Debtors {currency}",
			"company": company,
			"parent_account": parent,
			"account_currency": currency,
			"account_type": "Receivable",
			"is_group": 0,
		}
	)
	account.flags.ignore_permissions = True
	account.insert(ignore_permissions=True)
	return account.name
