import copy
import json
import os
from typing import ClassVar

import frappe
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.unicommerce.constants import PRODUCT_CATEGORY_FIELD, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.doctype.unicommerce_settings.unicommerce_settings import (
	setup_custom_fields,
)


class TestCase(IntegrationTestCase):
	config: ClassVar = {
		"is_enabled": 1,
		"enable_inventory_sync": 1,
		"use_stock_entry_for_grn": 1,
		"vendor_code": "ERP",
		"default_customer_group": "Individual",
		"warehouse_mapping": [
			{"unicommerce_facility_code": "Test-123", "erpnext_warehouse": "Stores - WP", "enabled": 1},
			{"unicommerce_facility_code": "B", "erpnext_warehouse": "Work In Progress - WP", "enabled": 1},
		],
	}

	@classmethod
	def setUpClass(cls):
		# Call parent first to auto-generate standard test records
		super().setUpClass()

		# Then create our custom test data
		cls.create_test_data()

		# Now configure Unicommerce settings
		settings = frappe.get_doc(SETTINGS_DOCTYPE)

		# remember config
		cls.old_config = copy.deepcopy(cls.config)
		for key in cls.old_config:
			cls.old_config[key] = getattr(settings, key)

		cls.old_config["warehouse_mapping"] = []
		for wh_map in settings.warehouse_mapping:
			keys_to_retain = ["unicommerce_facility_code", "erpnext_warehouse", "enabled"]
			cls.old_config["warehouse_mapping"].append({k: wh_map.get(k) for k in keys_to_retain})

		# change config
		for key, value in cls.config.items():
			setattr(settings, key, value)

		settings.warehouse_mapping = []
		for wh_map in cls.config["warehouse_mapping"]:
			settings.append("warehouse_mapping", wh_map)

		settings.flags.ignore_validate = True  # to prevent hitting the API
		settings.flags.ignore_mandatory = True
		settings.save()
		setup_custom_fields()
		_setup_test_item_categories()
		frappe.db.set_value("Stock Settings", None, "allow_negative_stock", 1)

	@classmethod
	def create_test_data(cls):
		"""Create required test data BEFORE parent setUpClass"""
		from ecommerce_integrations.tests.utils import EcommerceTestSuite

		# Create prerequisite data
		EcommerceTestSuite.create_warehouse_types()
		EcommerceTestSuite.create_customer_groups()
		EcommerceTestSuite.create_test_company()
		EcommerceTestSuite.create_wp_warehouses()

		# Create Unicommerce Channels manually BEFORE parent setUpClass
		cls.create_unicommerce_channels()
		frappe.db.commit()

	@classmethod
	def create_unicommerce_channels(cls):
		"""Create test Unicommerce Channels"""
		channels = [
			{
				"doctype": "Unicommerce Channel",
				"channel_id": "RAINFOREST",
				"display_name": "Amazon",
				"enabled": 1,
				"company": "Wind Power LLC",
				"warehouse": "Stores - WP",
				"return_warehouse": "Stores - WP",
				"customer_group": "Individual",
				"fnf_account": "Freight and Forwarding Charges - WP",
				"cod_account": "Freight and Forwarding Charges - WP",
				"igst_account": "Output Tax GST - WP",
				"cgst_account": "Output Tax GST - WP",
				"sgst_account": "Output Tax GST - WP",
				"ugst_account": "Output Tax GST - WP",
				"tcs_account": "Output Tax GST - WP",
				"cost_center": "Main - WP",
				"cash_or_bank_account": "Cash - WP",
				"gift_wrap_account": "Miscellaneous Expenses - WP",
			},
		]

		for channel_data in channels:
			if not frappe.db.exists("Unicommerce Channel", channel_data["channel_id"]):
				try:
					frappe.get_doc(channel_data).insert(ignore_if_duplicate=True)
				except Exception as e:
					print(f"Failed to create channel: {e}")

	@classmethod
	def tearDownClass(cls):
		# restore config
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		for key, value in cls.old_config.items():
			setattr(settings, key, value)

		settings.warehouse_mapping = []
		for wh_map in cls.old_config["warehouse_mapping"]:
			settings.append("warehouse_mapping", wh_map)

		settings.flags.ignore_validate = True  # to prevent hitting the API
		settings.flags.ignore_mandatory = True
		settings.save()
		frappe.db.set_value("Stock Settings", None, "allow_negative_stock", 0)

	def load_fixture(self, name):
		with open(os.path.dirname(__file__) + f"/fixtures/{name}.json", "rb") as f:
			data = f.read()
		return json.loads(data)


def _setup_test_item_categories():
	frappe.get_doc(
		{"doctype": "Item Group", PRODUCT_CATEGORY_FIELD: "TESTCAT", "item_group_name": "Test category"}
	).insert(ignore_if_duplicate=True)
	frappe.db.set_value("Item Group", "Products", PRODUCT_CATEGORY_FIELD, "Products")
