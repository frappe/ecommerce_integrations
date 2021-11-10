# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class UnicommercePackageType(Document):
	def validate(self):
		self.__update_title()
		self.__validate_sizes()

	def __update_title(self):
		self.title = f"{self.package_type}: {self.length}x{self.width}x{self.height}"

	def __validate_sizes(self):
		fields = ["length", "width", "height"]

		for field in fields:
			if cint(self.get(field)) <= 0:
				frappe.throw(frappe._("Positive value required for {}").format(field))
