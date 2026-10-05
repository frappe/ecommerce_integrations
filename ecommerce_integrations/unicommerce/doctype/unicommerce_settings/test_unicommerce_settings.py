# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import frappe
import responses
from frappe.utils import now, now_datetime

from ecommerce_integrations.unicommerce.constants import SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.tests.utils import TestCase


class TestUnicommerceSettings(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		settings.unicommerce_site = "demostaging.unicommerce.com"
		settings.username = "frappe"
		settings.password = "hunter2"

		cls.settings = settings

	@responses.activate
	def test_authentication(self):
		"""requirement: When saved the system get acess/refresh tokens from unicommerce."""

		responses.add(
			responses.GET,
			"https://demostaging.unicommerce.com/oauth/token?grant_type=password&username=frappe&password=hunter2&client_id=my-trusted-client",
			json=self.load_fixture("authentication"),
			status=200,
			match_querystring=True,
		)

		self.settings.update_tokens()

		self.assertEqual(self.settings.access_token, "1211cf66-d9b3-498b-a8a4-04c76578b72e")
		self.assertEqual(self.settings.refresh_token, "18f96b68-bdf4-4c5f-93f2-16e2c6e674c6")
		self.assertEqual(self.settings.token_type, "bearer")
		self.assertTrue(str(self.settings.expires_on) > now())

	@responses.activate
	def test_failed_auth(self):
		"""requirement: When improper credentials are provided, system throws error."""

		# failure case
		responses.add(responses.GET, "https://demostaging.unicommerce.com/oauth/token", json={}, status=401)
		self.assertRaises(frappe.ValidationError, self.settings.update_tokens)

	@responses.activate
	def test_refresh_tokens(self):
		"""requirement: The system has functionality to refresh token periodically. This is used by UnicommerceAPIClient to ensure that token is valid before using it."""
		url = "https://demostaging.unicommerce.com/oauth/token?grant_type=refresh_token&client_id=my-trusted-client&refresh_token=REFRESH_TOKEN"
		responses.add(
			responses.GET,
			url,
			json=self.load_fixture("authentication"),
			status=200,
			match_querystring=True,
		)

		self.settings.expires_on = now_datetime()  # to trigger refresh
		self.settings.refresh_token = "REFRESH_TOKEN"
		self.settings.update_tokens(grant_type="refresh_token")

		self.assertEqual(self.settings.access_token, "1211cf66-d9b3-498b-a8a4-04c76578b72e")
		self.assertEqual(self.settings.refresh_token, "18f96b68-bdf4-4c5f-93f2-16e2c6e674c6")
		self.assertEqual(self.settings.token_type, "bearer")
		self.assertTrue(str(self.settings.expires_on) > now())
		self.assertTrue(responses.assert_call_count(url, 1))
