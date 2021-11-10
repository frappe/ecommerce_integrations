# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.model.document import Document


class UnicommerceChannel(Document):
	def validate(self):
		self.__check_compnay()

	def __check_compnay(self):
		company_fields = {
			"warehouse": "Warehouse",
			"fnf_account": "Account",
			"cod_account": "Account",
			"gift_wrap_account": "Account",
			"igst_account": "Account",
			"cgst_account": "Account",
			"sgst_account": "Account",
			"ugst_account": "Account",
			"tcs_account": "Account",
			"cash_or_bank_account": "Account",
			"cost_center": "Cost Center",
		}

		for field, doctype in company_fields.items():
			if self.company != frappe.db.get_value(doctype, self.get(field), "company", cache=True):
				frappe.throw(
					_("{}: {} does not belong to company {}").format(doctype, self.get(field), self.company)
				)
