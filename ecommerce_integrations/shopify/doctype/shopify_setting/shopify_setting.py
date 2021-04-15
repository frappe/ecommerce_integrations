# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

from ecommerce_integrations.controllers.setting import SettingController
from ecommerce_integrations.shopify import connection


class ShopifySetting(SettingController):

	def is_enabled(self) -> bool:
		return bool(self.enable_shopify)


	def validate(self):
		self.handle_webhooks()


	def handle_webhooks(self):
		if self.is_enabled() and not self.webhooks:
			new_webhooks = connection.register_webhooks()

			for webhook in new_webhooks:
				self.append("webhooks", {
					"webhook_id": webhook.id,
					"method": webhook.topic
				})

		elif not self.is_enabled():
			connection.unregister_webhooks()

			self.webhooks = list()  # remove all webhooks
