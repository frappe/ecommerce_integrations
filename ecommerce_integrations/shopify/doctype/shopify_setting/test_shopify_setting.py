# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest

import frappe

from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SUPPLIER_ID_FIELD,
)

from .shopify_setting import setup_custom_fields


class TestShopifySetting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql(
			"""delete from `tabCustom Field`
			where name like '%shopify%'"""
		)

	def test_custom_field_creation(self):
		setup_custom_fields()

		created_fields = frappe.get_all(
			"Custom Field",
			filters={"fieldname": ["LIKE", "%shopify%"]},
			fields="fieldName",
			as_list=True,
			order_by=None,
		)

		required_fields = set(
			[
				ADDRESS_ID_FIELD,
				CUSTOMER_ID_FIELD,
				FULLFILLMENT_ID_FIELD,
				ITEM_SELLING_RATE_FIELD,
				ORDER_ID_FIELD,
				ORDER_NUMBER_FIELD,
				ORDER_STATUS_FIELD,
				SUPPLIER_ID_FIELD,
				ORDER_ITEM_DISCOUNT_FIELD,
			]
		)

		self.assertGreaterEqual(len(created_fields), 13)
		created_fields_set = {d[0] for d in created_fields}

		self.assertEqual(created_fields_set, required_fields)
