# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from typing import Optional

class EcommerceItem(Document):

	def validate(self):
		self.check_mandatory()
		self.check_duplicate()

	def check_mandatory(self):
		if self.has_variants and ((not self.variant_id) or (not self.variant_of)):
			raise frappe.ValidationError("variant_id and variant_of required")

	def check_duplicate(self):
		filter = {
				"erpnext_item_code": self.erpnext_item_code,
				"integration": self.integration,
				"integration_item_code" : self.integration_item_code
			}

		if self.has_variants:
			filter.update({ "variant_id": self.variant_id })

		if frappe.db.exists("Ecommerce Item", filter):
			raise frappe.DuplicateEntryError(_("Ecommerce item already exists"))



def is_synced(integration: str, integration_item_code: str,
		variant_id: Optional[str]= None) -> bool:
	""" Check if item is synced with integration.

		variant_id is optional. Use variant_id to check if particular variant is synced.
		E.g.
			integration: shopify,
			integration_item_code: TSHIRT
			variant_id: red_t_shirt
	"""

	filter = {
			"integration" : integration,
			"integration_item_code" : integration_item_code
		}
	if variant_id:
		filter.update({ "variant_id": variant_id })

	return bool(frappe.db.exists("Ecommerce Item", filter))


def get_erpnext_item(integration: str, integration_item_code: str,
		variant_id: Optional[str]= None):

	filter = {
			"integration" : integration,
			"integration_item_code" : integration_item_code
		}
	if variant_id:
		filter.update({ "variant_id": variant_id })

	item_code = frappe.db.get_value("Ecommerce Item", filter, fieldname="erpnext_item_code")

	if item_code:
		return frappe.get_doc("Item", item_code)

	return None


def get_integration_item_code(erpnext_item_code: str, integration: str) -> str:
	filter = {
			"integration" : integration,
			"erpnext_item_code" : erpnext_item_code
		}

	integration_item_code = frappe.db.get_value("Ecommerce Item", filter, fieldname="integration_item_code")

	return integration_item_code
