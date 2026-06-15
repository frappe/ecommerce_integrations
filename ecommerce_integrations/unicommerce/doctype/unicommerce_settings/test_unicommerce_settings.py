# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import frappe
import responses
from frappe.utils import now, now_datetime
from responses.matchers import query_param_matcher

from ecommerce_integrations.unicommerce.constants import SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.tests.utils import UnicommerceTestSuite

SITE = "demostaging.unicommerce.com"
TOKEN_URL = f"https://{SITE}/oauth/token"


class TestUnicommerceSettings(UnicommerceTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		settings.unicommerce_site = SITE
		settings.username = "frappe"
		settings.password = "hunter2"

		cls.settings = settings

	def fake_token(self, params=None, **kwargs):
		"""Register a fake oauth token response.

		`params` if provided is matched against the request's query string."""
		kwargs.setdefault("json", self.load_fixture("authentication"))
		kwargs.setdefault("status", 200)
		if params is not None:
			kwargs["match"] = [query_param_matcher(params)]

		responses.add(responses.GET, TOKEN_URL, **kwargs)

	def assert_tokens_synced(self):
		self.assertEqual(self.settings.access_token, "1211cf66-d9b3-498b-a8a4-04c76578b72e")
		self.assertEqual(self.settings.refresh_token, "18f96b68-bdf4-4c5f-93f2-16e2c6e674c6")
		self.assertEqual(self.settings.token_type, "bearer")
		self.assertGreater(str(self.settings.expires_on), now())

	@responses.activate
	def test_authentication(self):
		"""requirement: When saved the system gets access/refresh tokens from unicommerce."""
		self.fake_token(
			{
				"grant_type": "password",
				"username": "frappe",
				"password": "hunter2",
				"client_id": "my-trusted-client",
			}
		)

		self.settings.update_tokens()

		self.assert_tokens_synced()

	@responses.activate
	def test_failed_auth(self):
		"""requirement: When improper credentials are provided, system throws error."""
		self.fake_token(json={}, status=401)
		self.assertRaises(frappe.ValidationError, self.settings.update_tokens)

	@responses.activate
	def test_refresh_tokens(self):
		"""requirement: The system refreshes tokens periodically. UnicommerceAPIClient uses
		this to ensure the token is valid before using it."""
		self.fake_token(
			{
				"grant_type": "refresh_token",
				"client_id": "my-trusted-client",
				"refresh_token": "REFRESH_TOKEN",
			}
		)

		self.settings.expires_on = now_datetime()  # to trigger refresh
		self.settings.refresh_token = "REFRESH_TOKEN"
		self.settings.update_tokens(grant_type="refresh_token")

		self.assert_tokens_synced()
		self.assertEqual(len(responses.calls), 1)
