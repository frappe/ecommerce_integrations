import copy
import json
import os
import unittest
from typing import ClassVar

import frappe

from ecommerce_integrations.unicommerce.constants import PRODUCT_CATEGORY_FIELD, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.doctype.unicommerce_settings.unicommerce_settings import (
	setup_custom_fields,
)


class TestCase(unittest.TestCase):
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
