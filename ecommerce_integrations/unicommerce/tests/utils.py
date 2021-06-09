import copy
import json
import os
import unittest

import frappe

from ecommerce_integrations.unicommerce.constants import SETTINGS_DOCTYPE


class TestCase(unittest.TestCase):
	config = {
		"username": "frappe",
		"password": "hunter2",
		"unicommerce_site": "demostaging.unicommerce.com",
		"access_token": "AUTH_TOKEN",
		"is_enabled": 1,
	}

	@classmethod
	def setUpClass(cls):
		settings = frappe.get_doc(SETTINGS_DOCTYPE)

		cls.old_config = copy.deepcopy(cls.config)
		for key in cls.old_config:
			cls.old_config[key] = getattr(settings, key)
		if settings.password:
			cls.old_config["password"] = settings.get_password("password")
		if settings.access_token:
			cls.old_config["access_token"] = settings.get_password("access_token")

		for key, value in cls.config.items():
			setattr(settings, key, value)

		settings.flags.ignore_validate = True  # to prevent hitting the API
		settings.flags.ignore_mandatory = True
		settings.save()

	@classmethod
	def tearDownClass(cls):
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		for key, value in cls.old_config.items():
			setattr(settings, key, value)

		settings.flags.ignore_validate = True  # to prevent hitting the API
		settings.flags.ignore_mandatory = True
		settings.save()

	def load_fixture(self, name):
		with open(os.path.dirname(__file__) + f"/fixtures/{name}.json", "rb") as f:
			data = f.read()
		return json.loads(data)
