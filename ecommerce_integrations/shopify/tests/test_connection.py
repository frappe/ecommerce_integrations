# Copyright (c) 2021, Frappe and Contributors
# See license.txt

import unittest
from ecommerce_integrations.shopify import connection
from shopify.resources import Webhook

class TestShopifyConnection(unittest.TestCase):

	# TODO: mock out dependency on Shopify settings

	def test_register_webhooks(self):
		webhooks = connection.register_webhooks()

		self.assertEqual(len(webhooks), len(connection.WEBHOOK_EVENTS))

		wh_topics = [wh.topic for wh in webhooks]
		self.assertEqual(sorted(wh_topics), sorted(connection.WEBHOOK_EVENTS))




	@connection.temp_shopify_session
	def test_unregister_webhooks(self):

		connection.unregister_webhooks()

		callback_url = connection.get_callback_url()

		for wh in Webhook.find():
			self.assertNotEqual(wh.address, callback_url)


