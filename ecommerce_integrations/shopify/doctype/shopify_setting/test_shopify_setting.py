# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

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
from ecommerce_integrations.tests.utils import EcommerceTestSuite

from .shopify_setting import setup_custom_fields


class TestShopifySetting(EcommerceTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.delete("Custom Field", {"fieldname": ["like", "%shopify%"]})

	def test_custom_field_creation(self):
		setup_custom_fields()

		created_fields = frappe.get_all(
			"Custom Field",
			filters={"fieldname": ["like", "%shopify%"]},
			pluck="fieldname",
		)

		required_fields = {
			ADDRESS_ID_FIELD,
			CUSTOMER_ID_FIELD,
			FULLFILLMENT_ID_FIELD,
			ITEM_SELLING_RATE_FIELD,
			ORDER_ID_FIELD,
			ORDER_ITEM_DISCOUNT_FIELD,
			ORDER_NUMBER_FIELD,
			ORDER_STATUS_FIELD,
			SUPPLIER_ID_FIELD,
		}

		self.assertGreaterEqual(len(created_fields), 13)
		self.assertEqual(set(created_fields), required_fields)
