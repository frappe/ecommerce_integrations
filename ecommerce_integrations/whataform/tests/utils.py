import json
import os
import sys
import unittest
from unittest.mock import patch

import frappe
from erpnext import get_default_cost_center

from ecommerce_integrations.whataform.constants import SETTING_DOCTYPE


class TestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		setting = frappe.get_doc(SETTING_DOCTYPE)
		setting.update(
			{
				"enable_whataform": 1,
				"form_id": "1ba668db37b1dd",
				"customer_group": "_Test Customer Group 1",
				"company": "_Test Company",
				"warehouse": "_Test Warehouse - _TC",
				"sales_order_series": "SAL-ORD-.YYYY.-",
				"doctype": "Whataform Setting",
				"tax_master_template": "_Test Tax 1 - _TC",
				"shipping_rule": "_Test Shipping Rule",
				# identifying field mappings
				"first_name_field_key": "first_name",
				"last_name_field_key": "last_name",
				"email_field_key": "email",
				"whatsapp_field_key": "whatsapp",
				# address field mappings
				"address_line1_field_key": "address1",
				"address_line2_field_key": "address2",
				"zip_code_field_key": "zip_code",
				"city_field_key": "city",
				"country_field_key": "country",
				"google_addrss_field_key": "address",
				# warehouse mappings
				"location_field_key": "city",
				"whataform_warehouse_mapping": [
					{"whataform_location_value": "Botoga", "erpnext_warehouse": "_Test Warehouse 1 - _TC",},
				],
			}
		).save(ignore_permissions=True)

	def load_fixture(self, name, format="json"):
		with open(os.path.dirname(__file__) + "/data/%s.%s" % (name, format), "rb") as f:
			return json.load(f)
