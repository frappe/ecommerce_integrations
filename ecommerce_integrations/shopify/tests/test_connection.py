# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest

import frappe
from shopify.resources import Webhook
from shopify.session import Session

from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.constants import API_VERSION, SETTING_DOCTYPE


class TestShopifyConnection(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.setting = frappe.get_doc(SETTING_DOCTYPE)

	@unittest.skip("Can't run these tests in CI")
	def test_register_webhooks(self):
		webhooks = connection.register_webhooks(
			self.setting.shopify_url, self.setting.get_password("password")
		)

		self.assertEqual(len(webhooks), len(connection.WEBHOOK_EVENTS))

		wh_topics = [wh.topic for wh in webhooks]
		self.assertEqual(sorted(wh_topics), sorted(connection.WEBHOOK_EVENTS))

	@unittest.skip("Can't run these tests in CI")
	def test_unregister_webhooks(self):
		connection.unregister_webhooks(self.setting.shopify_url, self.setting.get_password("password"))

		callback_url = connection.get_callback_url()

		with Session.temp(self.setting.shopify_url, API_VERSION, self.setting.get_password("password")):
			for wh in Webhook.find():
				self.assertNotEqual(wh.address, callback_url)
