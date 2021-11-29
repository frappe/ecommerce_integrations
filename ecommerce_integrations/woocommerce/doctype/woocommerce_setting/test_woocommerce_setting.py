# Copyright (c) 2021, Frappe and Contributors
# See license.txt

import unittest

import frappe

from ecommerce_integrations.woocommerce.constants import MODULE_NAME, SETTINGS_DOCTYPE
from ecommerce_integrations.woocommerce.doctype.woocommerce_setting.woocommerce_setting import (
	WoocommerceSetting,
	generate_secret,
)

class TestWoocommerceSetting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		settings.woocommerce_server_url = "http://localhost:8080/wordpress"
		settings.api_consumer_key = "ck_2b74b3b862a3bd168735ccbf89cdc31087e5fcd3"
		settings.api_consumer_secret = "cs_63735c2528de30a8ab8f26e10202099b24112f60"

		cls.settings = settings

	@unittest.skip("Can't run these tests in CI")
	def test_webhook_creation(self):
		self.settings.create_webhook_url()
		required_url = "https://8a48-103-58-153-217.ngrok.io/api/method/ecommerce_integrations.woocommerce.woocommerce_connection.order"
		self.assertEqual(self.settings.endpoint, required_url)

	@unittest.skip("Can't run these tests in CI")
	def test_generate_secret(self):
		generate_secret()
		self.assertIsNotNone(self.settings.secret)
