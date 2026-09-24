# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import responses

import frappe
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

	def test_unknown_charge_is_rejected(self):
		"""requirement: A charge that bills no Unicommerce tax head can't be a charge item."""
		settings = self._settings_with_charges({"charge": "Loyalty Charges", "tax_rate": 18.0})

		self.assertRaises(frappe.ValidationError, settings.validate_charge_items)

	def test_charge_item_must_not_maintain_stock(self):
		"""requirement: Stock item can't be a charge item."""
		settings = self._settings_with_charges({"charge": "Gift Wrap Charges", "tax_rate": 18.0})
		settings.charge_items[0].item_code = self._charge_item(18.0, is_stock_item=1)

		self.assertRaises(frappe.ValidationError, settings.validate_charge_items)

	def test_charge_item_at_multiple_tax_rates_is_rejected(self):
		"""requirement: Charge item can't be used at more than one tax rate."""
		item_code = self._charge_item(5.0)
		settings = self._settings_with_charges(
			{"charge": "Cash On Delivery Charges", "tax_rate": 5.0, "item_code": item_code},
			{"charge": "Cash On Delivery Charges", "tax_rate": 18.0, "item_code": item_code},
		)

		self.assertRaises(frappe.ValidationError, settings.validate_charge_items)

	def test_duplicate_charge_and_rate_is_rejected(self):
		"""requirement: Charge and tax rate can't be repeated."""
		settings = self._settings_with_charges(
			{"charge": "Gift Wrap Charges", "tax_rate": 18.0},
			{"charge": "Gift Wrap Charges", "tax_rate": 18.0},
		)

		self.assertRaises(frappe.ValidationError, settings.validate_charge_items)

	def test_same_charge_at_different_rates_is_allowed(self):
		"""requirement: Same charge can be added for different tax rates, and charges can share an item at one rate."""
		settings = self._settings_with_charges(
			{"charge": "Gift Wrap Charges", "tax_rate": 18.0},
			{"charge": "Gift Wrap Charges", "tax_rate": 5.0},
			{"charge": "Cash On Delivery Charges", "tax_rate": 18.0},
		)

		settings.validate_charge_items()

	def test_charge_items_are_left_alone_when_charges_are_not_billed_as_items(self):
		"""requirement: Charge items are not validated when disabled."""
		settings = self._settings_with_charges(
			{"charge": "Gift Wrap Charges", "tax_rate": 18.0},
			{"charge": "Gift Wrap Charges", "tax_rate": 18.0},
		)
		settings.add_charges_as_items = 0

		settings.validate_charge_items()

	def _settings_with_charges(self, *rows):
		"""Settings with charges billed as items."""
		settings = frappe.get_doc(SETTINGS_DOCTYPE)
		settings.add_charges_as_items = 1
		settings.charge_items = []

		for row in rows:
			settings.append("charge_items", {"item_code": self._charge_item(row["tax_rate"]), **row})

		return settings

	def _charge_item(self, tax_rate: float, is_stock_item: int = 0) -> str:
		item_code = f"_Test Unicommerce Charge Item {tax_rate:g} {is_stock_item}"

		if not frappe.db.exists("Item", item_code):
			item = frappe.get_doc(
				doctype="Item",
				item_code=item_code,
				item_group="Products",
				stock_uom="Nos",
				is_stock_item=is_stock_item,
			)
			# skip uploading the item to other integrations enabled by their tests
			item.flags.from_integration = True
			item.insert()

		return item_code
