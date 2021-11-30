import json
import os
import unittest

import frappe
from frappe import _

from ecommerce_integrations.woocommerce.constants import SETTINGS_DOCTYPE
from ecommerce_integrations.woocommerce.woocommerce_connection import (
	create_sales_order,
	link_customer_and_address,
	link_items,
	verify_request,
)


class TestWooCommerceConnection(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

	# @unittest.skip("Can't run these tests in CI")
	def test_verify_incorrect_header(self):
		self.assertRaises(Exception, verify_request)

	# @unittest.skip("Can't run these tests in CI")
	def test_order_creation(self):

		with open(os.path.join(os.path.dirname(__file__), "test_order.json")) as f:
			params = json.load(f)
		sys_lang = frappe.get_single("System Settings").language or "en"
		woocommerce_settings = frappe.get_doc(SETTINGS_DOCTYPE)
		raw_billing_data = params.get("billing")
		raw_shipping_data = params.get("shipping")
		customer_name = raw_billing_data.get("first_name") + " " + raw_billing_data.get("last_name")
		link_customer_and_address(raw_billing_data, raw_shipping_data, customer_name)
		entry_exists = frappe.get_value(
			"Customer", {"woocommerce_email": raw_billing_data.get("email"), "name": customer_name}
		)
		self.assertTrue(entry_exists, "customer not added")
		link_items(params.get("line_items"), woocommerce_settings, sys_lang)
		entry_exists = frappe.get_value(
			"Ecommerce Item",
			{
				"erpnext_item_code": _("woocommerce - {0}", sys_lang).format(
					params.get("line_items")[0].get("product_id")
				)
			},
		)
		self.assertTrue(entry_exists, "ecoomm not added")
		entry_exists = frappe.get_value("Item", {"item_name": params.get("line_items")[0].get("name")})
		self.assertTrue(entry_exists, "item not added")
		create_sales_order(params, woocommerce_settings, customer_name, sys_lang)
		entry_exists = frappe.get_value("Sales Order", {"woocommerce_id": params.get("id")})
		self.assertTrue(entry_exists, "Sales order not added")
