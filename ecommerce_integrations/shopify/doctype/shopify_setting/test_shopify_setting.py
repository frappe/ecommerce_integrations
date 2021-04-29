# Copyright (c) 2021, Frappe and Contributors
# See license.txt

import frappe
import unittest


from .shopify_setting import setup_custom_fields
from ecommerce_integrations.shopify.constants import (
	CUSTOMER_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	FULLFILLMENT_ID_FIELD,
	SUPPLIER_ID_FIELD,
	ADDRESS_ID_FIELD,
)


class TestShopifySetting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql(
			"""delete from `tabCustom Field`
			where name like '%shopify%'"""
		)

	def test_custom_field_creation(self):

		setup_custom_fields()

		created_fields = frappe.db.get_list(
			"Custom Field", filters={"fieldname": ["LIKE", "%shopify%"]}, fields="fieldName", as_list=True
		)

		required_fields = set(
			[
				CUSTOMER_ID_FIELD,
				ORDER_ID_FIELD,
				ORDER_NUMBER_FIELD,
				FULLFILLMENT_ID_FIELD,
				SUPPLIER_ID_FIELD,
				ADDRESS_ID_FIELD,
			]
		)

		self.assertEqual(len(created_fields), 10)
		created_fields_set = set(d[0] for d in created_fields)

		self.assertEqual(created_fields_set, required_fields)
