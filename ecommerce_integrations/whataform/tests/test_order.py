# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import json
import unittest

import frappe
from frappe.tests.utils import debug_on

from ecommerce_integrations.whataform.connection import process_request
from ecommerce_integrations.whataform.constants import ORDER_ID_FIELD

from .utils import TestCase


class TestOrder(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# ei = frappe.get_doc({
		#     "doctype": "Ecommerce Item",
		#     "integration": "whataform",
		#     "erpnext_item_code": "_Test Item",
		#     "sku": "TESTsku",
		# })
		# ei.insert(ignore_mandatory=True)

	debug_on()

	def test_normal_message(self):
		data = self.load_fixture("normal_message")
		data["detail_order"]["1"]["sku"] = None
		process_request(data, "message")

		eil = frappe.get_last_doc("Ecommerce Integration Log")
		self.assertEqual(eil.get_title(), "No SKU in data")
		# cleanup, else the log would simply get updated
		del frappe.flags.request_id

		# ensure customer is properly created regardless
		customer = frappe.get_last_doc("Customer")
		self.assertIsNotNone(customer.customer_primary_address, "Ensure customer has primary address")
		self.assertIsNotNone(customer.customer_primary_contact, "Ensure customer has primary contact")

		data = self.load_fixture("normal_message")
		process_request(data, "message")

		so = frappe.get_last_doc("Sales Order")
		self.assertEqual(so.get(ORDER_ID_FIELD), data.get("message"), "Ensure SO was created and linked")
		self.assertEqual(so.customer_name, customer.name, "Ensure customer was mapped")
