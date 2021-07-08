import copy
import json
import os
import unittest

import frappe

from ecommerce_integrations.unicommerce.constants import PRODUCT_CATEGORY_FIELD, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.doctype.unicommerce_settings.unicommerce_settings import (
	setup_custom_fields,
)


class TestCase(unittest.TestCase):
	config = {
		"username": "frappe",
		"password": "hunter2",
		"unicommerce_site": "demostaging.unicommerce.com",
		"access_token": "AUTH_TOKEN",
		"is_enabled": 1,
		"enable_inventory_sync": 1,
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
		if settings.password:
			cls.old_config["password"] = settings.get_password("password")
		if settings.access_token:
			cls.old_config["access_token"] = settings.get_password("access_token")

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

	def load_fixture(self, name):
		with open(os.path.dirname(__file__) + f"/fixtures/{name}.json", "rb") as f:
			data = f.read()
		return json.loads(data)


def _setup_test_item_categories():
	frappe.get_doc(
		{"doctype": "Item Group", PRODUCT_CATEGORY_FIELD: "TESTCAT", "item_group_name": "Test category"}
	).insert(ignore_if_duplicate=True)
	frappe.db.set_value("Item Group", "Products", PRODUCT_CATEGORY_FIELD, "Products")
