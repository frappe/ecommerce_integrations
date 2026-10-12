# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import get_decrypted_password

from ecommerce_integrations.shopify.connection import _validate_request, get_access_token
from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, OAUTH_AUTH_METHOD

ACCOUNT_MODULE = "ecommerce_integrations.shopify.doctype.shopify_account.shopify_account"
SHOP = "oauth.myshopify.com"


def token_response(token="token-1", expires_in=86399, status=200):
	response = MagicMock(status_code=status)
	response.json.return_value = {"access_token": token, "expires_in": expires_in}
	response.raise_for_status.return_value = None
	return response


def error_response(body):
	response = requests.Response()
	response.status_code = 400
	response._content = json.dumps(body).encode()
	return response


class TestShopifyOAuth(IntegrationTestCase):
	def setUp(self):
		for name in frappe.get_all(ACCOUNT_DOCTYPE, {"enable_shopify": 1}, pluck="name"):
			frappe.db.set_value(ACCOUNT_DOCTYPE, name, "enable_shopify", 0)

	def tearDown(self):
		frappe.db.rollback()

	def make_oauth_account(self, enabled=1):
		with (
			patch(f"{ACCOUNT_MODULE}.ShopifyAccount._handle_webhooks"),
			patch(f"{ACCOUNT_MODULE}.setup_custom_fields"),
			patch("requests.post", return_value=token_response("token-on-save")) as post,
		):
			account = frappe.get_doc(
				{
					"doctype": ACCOUNT_DOCTYPE,
					"shopify_url": SHOP,
					"enable_shopify": enabled,
					"authentication_method": OAUTH_AUTH_METHOD,
					"client_id": "client-id",
					"client_secret": "client-secret",
					"is_old_data_migrated": 1,
				}
			)
			account.flags.ignore_links = True
			account.flags.ignore_mandatory = True
			account.insert(ignore_permissions=True)
		return account, post

	def test_enabling_requests_and_stores_a_token(self):
		account, post = self.make_oauth_account()

		post.assert_called_once()
		self.assertEqual(post.call_args.args[0], f"https://{SHOP}/admin/oauth/access_token")
		self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "client_credentials")
		self.assertEqual(post.call_args.kwargs["data"]["client_secret"], "client-secret")
		self.assertEqual(get_decrypted_password(ACCOUNT_DOCTYPE, SHOP, "oauth_access_token"), "token-on-save")
		self.assertTrue(frappe.db.get_value(ACCOUNT_DOCTYPE, SHOP, "token_expires_at"))

	def test_valid_token_is_reused(self):
		account, _ = self.make_oauth_account()
		account = frappe.get_doc(ACCOUNT_DOCTYPE, SHOP)

		with patch("requests.post") as post:
			self.assertEqual(get_access_token(account), "token-on-save")
		post.assert_not_called()

	def test_expiring_token_is_renewed_and_stored(self):
		account, _ = self.make_oauth_account()
		frappe.db.set_value(ACCOUNT_DOCTYPE, SHOP, "token_expires_at", add_to_date(now_datetime(), minutes=2))
		account = frappe.get_doc(ACCOUNT_DOCTYPE, SHOP)

		with patch("requests.post", return_value=token_response("token-2")) as post:
			self.assertEqual(get_access_token(account), "token-2")
		post.assert_called_once()
		self.assertEqual(get_decrypted_password(ACCOUNT_DOCTYPE, SHOP, "oauth_access_token"), "token-2")

	def test_credentials_changed_while_disabled_get_a_new_token_on_enable(self):
		self.make_oauth_account()
		with patch(f"{ACCOUNT_MODULE}.setup_custom_fields"):
			account = frappe.get_doc(ACCOUNT_DOCTYPE, SHOP)
			account.enable_shopify = 0
			account.save()

			account.client_secret = "secret-of-the-new-app"
			account.save()
			self.assertFalse(account.token_expires_at)

			account.enable_shopify = 1
			with (
				patch(f"{ACCOUNT_MODULE}.ShopifyAccount._handle_webhooks"),
				patch("requests.post", return_value=token_response("token-of-the-new-app")) as post,
			):
				account.save()

		self.assertEqual(post.call_args.kwargs["data"]["client_secret"], "secret-of-the-new-app")
		self.assertEqual(get_access_token(frappe.get_doc(ACCOUNT_DOCTYPE, SHOP)), "token-of-the-new-app")

	def test_rejected_credentials_fail_the_save_and_hide_the_secret(self):
		rejected = requests.exceptions.HTTPError(
			response=error_response({"error": "invalid_client", "error_description": "Client unknown"})
		)
		response = MagicMock()
		response.raise_for_status.side_effect = rejected

		with (
			patch("requests.post", return_value=response),
			patch("time.sleep"),
			patch("ecommerce_integrations.shopify.oauth.create_shopify_log") as log,
		):
			self.assertRaises(frappe.ValidationError, self.make_oauth_account_without_token_mock)

		self.assertEqual(log.call_args.kwargs["request_data"]["client_secret"], "REDACTED")
		self.assertEqual(log.call_args.kwargs["exception"], "Client unknown")

	def make_oauth_account_without_token_mock(self):
		with (
			patch(f"{ACCOUNT_MODULE}.ShopifyAccount._handle_webhooks"),
			patch(f"{ACCOUNT_MODULE}.setup_custom_fields"),
		):
			account = frappe.get_doc(
				{
					"doctype": ACCOUNT_DOCTYPE,
					"shopify_url": SHOP,
					"enable_shopify": 1,
					"authentication_method": OAUTH_AUTH_METHOD,
					"client_id": "client-id",
					"client_secret": "wrong-secret",
				}
			)
			account.flags.ignore_links = True
			account.flags.ignore_mandatory = True
			account.insert(ignore_permissions=True)

	def test_oauth_needs_client_credentials(self):
		with patch(f"{ACCOUNT_MODULE}.setup_custom_fields"):
			account = frappe.get_doc(
				{
					"doctype": ACCOUNT_DOCTYPE,
					"shopify_url": SHOP,
					"enable_shopify": 1,
					"authentication_method": OAUTH_AUTH_METHOD,
					"client_id": "client-id",
				}
			)
			account.flags.ignore_mandatory = True
			self.assertRaises(frappe.ValidationError, account.insert, ignore_permissions=True)

	def test_webhooks_of_an_oauth_app_are_verified_with_the_client_secret(self):
		account, _ = self.make_oauth_account()
		body = b'{"id": 1}'

		def signed(secret):
			digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
			return frappe._dict(data=body), base64.b64encode(digest).decode()

		request, signature = signed("client-secret")
		_validate_request(request, signature, account)

		request, signature = signed("some-shared-secret")
		with patch("ecommerce_integrations.shopify.connection.create_shopify_log"):
			self.assertRaises(frappe.ValidationError, _validate_request, request, signature, account)

	def test_existing_accounts_keep_static_tokens(self):
		with (
			patch(f"{ACCOUNT_MODULE}.ShopifyAccount._handle_webhooks"),
			patch(f"{ACCOUNT_MODULE}.setup_custom_fields"),
			patch("requests.post") as post,
		):
			account = frappe.get_doc(
				{
					"doctype": ACCOUNT_DOCTYPE,
					"shopify_url": "static.myshopify.com",
					"enable_shopify": 1,
					"password": "static-token",
					"shared_secret": "shared",
					"is_old_data_migrated": 1,
				}
			).insert(ignore_permissions=True, ignore_links=True)

		post.assert_not_called()
		self.assertEqual(account.authentication_method, "Static Token")
		self.assertEqual(get_access_token(account), "static-token")
