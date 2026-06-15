# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest

import frappe
from shopify.resources import Webhook
from shopify.session import Session

from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.constants import API_VERSION, SETTING_DOCTYPE

from .utils import ShopifyTestSuite


class TestShopifyConnection(ShopifyTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setting = frappe.get_doc(SETTING_DOCTYPE)
		cls.password = cls.setting.get_password("password")

	@unittest.skip("Can't run these tests in CI")
	def test_register_webhooks(self):
		webhooks = connection.register_webhooks(self.setting.shopify_url, self.password)

		self.assertEqual(len(webhooks), len(connection.WEBHOOK_EVENTS))

		webhook_topics = sorted(webhook.topic for webhook in webhooks)
		self.assertEqual(webhook_topics, sorted(connection.WEBHOOK_EVENTS))

	@unittest.skip("Can't run these tests in CI")
	def test_unregister_webhooks(self):
		connection.unregister_webhooks(self.setting.shopify_url, self.password)

		callback_url = connection.get_callback_url()

		with Session.temp(self.setting.shopify_url, API_VERSION, self.password):
			for webhook in Webhook.find():
				self.assertNotEqual(webhook.address, callback_url)
