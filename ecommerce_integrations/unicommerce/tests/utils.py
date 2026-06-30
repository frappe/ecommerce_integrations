import json
from functools import wraps
from pathlib import Path
from typing import ClassVar

import frappe

from ecommerce_integrations.tests.utils import EcommerceTestSuite
from ecommerce_integrations.unicommerce.constants import PRODUCT_CATEGORY_FIELD, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.doctype.unicommerce_settings.unicommerce_settings import (
	setup_custom_fields,
)

WAREHOUSE_MAPPING_FIELDS = ("unicommerce_facility_code", "erpnext_warehouse", "enabled")


def enable_setting(fieldname, value=1):
	"""Decorator: temporarily set a Unicommerce Settings field for a single test.

	Uses set_single_value (writes to `tabSingles` directly), so it bypasses the
	doctype's mandatory/validate checks — unlike frappe's `change_settings`, which
	does a full `.save()` and fails on Unicommerce Settings' mandatory fields.
	"""

	def decorator(fn):
		@wraps(fn)
		def wrapper(self, *args, **kwargs):
			previous = frappe.db.get_single_value(SETTINGS_DOCTYPE, fieldname)
			frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, value)
			try:
				return fn(self, *args, **kwargs)
			finally:
				frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, previous)

		return wrapper

	return decorator


class UnicommerceTestSuite(EcommerceTestSuite):
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
		super().setUpClass()

		settings = frappe.get_doc(SETTINGS_DOCTYPE)

		cls.old_config = {key: settings.get(key) for key in cls.config}
		cls.old_config["warehouse_mapping"] = [
			{key: row.get(key) for key in WAREHOUSE_MAPPING_FIELDS} for row in settings.warehouse_mapping
		]

		cls.apply_config(cls.config)
		setup_custom_fields()
		_setup_test_item_categories()
		_setup_unicommerce_channels()
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

		# persist setup across per-test rollbacks done in tearDown
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		cls.apply_config(cls.old_config)
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)

		frappe.db.commit()  # nosemgrep

	@classmethod
	def apply_config(cls, config):
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		settings.update(config)
		settings.flags.ignore_validate = True  # to prevent hitting the API
		settings.flags.ignore_mandatory = True
		settings.save()

	def load_fixture(self, name):
		return json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_bytes())


def _setup_test_item_categories():
	frappe.get_doc(
		{"doctype": "Item Group", PRODUCT_CATEGORY_FIELD: "TESTCAT", "item_group_name": "Test category"}
	).insert(ignore_if_duplicate=True)
	frappe.db.set_value("Item Group", "Products", PRODUCT_CATEGORY_FIELD, "Products")


def _setup_unicommerce_channels():
	"""Create the test Unicommerce Channels from the doctype's test records.

	`make_test_records` is unreliable under the new test bootstrap, so the
	channels are created explicitly and committed in setUpClass to ensure they
	survive the per-test rollbacks done in tearDown."""
	channel_records = json.loads(
		Path(
			frappe.get_app_path(
				"ecommerce_integrations", "unicommerce", "doctype", "unicommerce_channel", "test_records.json"
			)
		).read_bytes()
	)
	for record in channel_records:
		frappe.get_doc(record).insert(ignore_if_duplicate=True)
