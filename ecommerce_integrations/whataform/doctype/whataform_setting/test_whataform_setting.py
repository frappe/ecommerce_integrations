# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest

import frappe

from ecommerce_integrations.whataform.constants import (
	CUSTOMER_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
)

from .whataform_setting import setup_custom_fields


class TestWhataformSetting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql(
			"""delete from `tabCustom Field`
			where name like '%whataform%'"""
		)

	def test_custom_field_creation(self):

		setup_custom_fields()

		created_fields = frappe.get_all(
			"Custom Field",
			filters={"fieldname": ["LIKE", "%whataform%"]},
			fields="fieldName",
			as_list=True,
			order_by=None,
		)

		required_fields = set(
			[CUSTOMER_ID_FIELD, ORDER_ID_FIELD, ORDER_NUMBER_FIELD, ORDER_ITEM_DISCOUNT_FIELD,]
		)

		self.assertGreaterEqual(len(created_fields), 8)
		created_fields_set = {d[0] for d in created_fields}

		self.assertEqual(created_fields_set, required_fields)
