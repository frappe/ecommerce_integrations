# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

class EcommerceItem(Document):

	def validate(self):
		already_exists =  frappe.db.exists("Ecommerce Item", {
				"erpnext_item_code": self.erpnext_item_code,
				"integration": self.integration,
				"integration_item_code" : self.integration_item_code
			})

		if already_exists:
			raise frappe.DuplicateEntryError(_("Ecommerce item already exists"))
