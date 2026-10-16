# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import base64
import hashlib
import hmac
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.utils import now_datetime


class TestIsTokenValid(unittest.TestCase):
	"""Pure logic — no Frappe DB needed."""

	def _import(self):
		from ecommerce_integrations.shopify.oauth import is_token_valid

		return is_token_valid

	def test_missing_expiry_returns_false(self):
		is_token_valid = self._import()
		self.assertFalse(is_token_valid(None))
		self.assertFalse(is_token_valid(""))

	def test_valid_token_returns_true(self):
		is_token_valid = self._import()
		future = now_datetime() + timedelta(hours=1)
		self.assertTrue(is_token_valid(future))

	def test_token_within_buffer_returns_false(self):
		"""Token expiring in 3 min (< 5-min buffer) must trigger refresh."""
		is_token_valid = self._import()
		near_expiry = now_datetime() + timedelta(minutes=3)
		self.assertFalse(is_token_valid(near_expiry))

	def test_expired_token_returns_false(self):
		is_token_valid = self._import()
		past = now_datetime() - timedelta(hours=1)
		self.assertFalse(is_token_valid(past))


class TestGetOauthTokenEndpoint(unittest.TestCase):
	"""URL construction — no HTTP or Frappe needed."""

	def _import(self):
		from ecommerce_integrations.shopify.oauth import get_oauth_token_endpoint

		return get_oauth_token_endpoint

	def test_plain_domain(self):
		fn = self._import()
		self.assertEqual(
			fn("example.myshopify.com"),
			"https://example.myshopify.com/admin/oauth/access_token",
		)

	def test_strips_https_prefix(self):
		fn = self._import()
		self.assertEqual(
			fn("https://example.myshopify.com"),
			"https://example.myshopify.com/admin/oauth/access_token",
		)

	def test_strips_http_prefix(self):
		fn = self._import()
		self.assertEqual(
			fn("http://example.myshopify.com"),
			"https://example.myshopify.com/admin/oauth/access_token",
		)

	def test_strips_trailing_slash(self):
		fn = self._import()
		self.assertEqual(
			fn("example.myshopify.com/"),
			"https://example.myshopify.com/admin/oauth/access_token",
		)

	def test_normalizes_hostname_case(self):
		fn = self._import()
		self.assertEqual(
			fn("HTTPS://Example-Store.myshopify.com/"),
			"https://example-store.myshopify.com/admin/oauth/access_token",
		)

	def test_rejects_non_shopify_or_ambiguous_urls(self):
		fn = self._import()
		invalid_urls = (
			"example.com",
			"example.myshopify.com.attacker.test",
			"attacker.test@example.myshopify.com",
			"example.myshopify.com:443",
			"example.myshopify.com/admin",
			"ftp://example.myshopify.com",
		)

		for url in invalid_urls:
			with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
				fn(url)


class TestGenerateOauthToken(unittest.TestCase):
	"""Mock requests.post — no real HTTP."""

	def _import(self):
		from ecommerce_integrations.shopify.oauth import generate_oauth_token

		return generate_oauth_token

	def _mock_response(self, status_code=200, json_data=None):
		mock_resp = MagicMock()
		mock_resp.status_code = status_code
		mock_resp.json.return_value = json_data or {
			"access_token": "shpat_test_token_abc123",
			"expires_in": 86399,
			"scope": "read_orders,write_orders",
		}
		if status_code >= 400:
			from requests.exceptions import HTTPError

			mock_resp.raise_for_status.side_effect = HTTPError(response=mock_resp)
		else:
			mock_resp.raise_for_status.return_value = None
		return mock_resp

	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_success_returns_token_data(self, mock_post, mock_log):
		mock_post.return_value = self._mock_response()
		generate_oauth_token = self._import()

		result = generate_oauth_token("example.myshopify.com", "client_id_1", "client_secret_1")

		self.assertEqual(result["access_token"], "shpat_test_token_abc123")
		self.assertEqual(result["expires_in"], 86399)
		mock_post.assert_called_once()
		call_kwargs = mock_post.call_args
		# Verify correct endpoint
		self.assertIn("example.myshopify.com", call_kwargs[0][0])
		# Verify payload
		payload = call_kwargs[1]["data"]
		self.assertEqual(payload["grant_type"], "client_credentials")
		self.assertEqual(payload["client_id"], "client_id_1")
		self.assertNotIn("client_secret_1", str(mock_log.call_args))  # secret not in logs

	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_http_401_raises_validation_error(self, mock_post, mock_log):
		mock_post.return_value = self._mock_response(
			status_code=401, json_data={"error": "invalid_client", "error_description": "Bad credentials"}
		)
		generate_oauth_token = self._import()

		with self.assertRaises(frappe.exceptions.ValidationError):
			generate_oauth_token("example.myshopify.com", "bad_id", "bad_secret")

	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_shop_not_permitted_explains_same_organization_requirement(self, mock_post, mock_log):
		mock_post.return_value = self._mock_response(
			status_code=400,
			json_data={
				"error": "shop_not_permitted",
				"error_description": "Client credentials cannot be performed on this shop.",
			},
		)
		generate_oauth_token = self._import()

		with self.assertRaises(frappe.ValidationError) as context:
			generate_oauth_token("example.myshopify.com", "client_id_1", "client_secret_1")

		self.assertIn("same Shopify organization", str(context.exception))

	@patch("requests.post", side_effect=requests.exceptions.Timeout("timed out"))
	def test_timeout_is_preserved_for_retry(self, mock_post):
		generate_oauth_token = self._import()

		with self.assertRaises(requests.exceptions.Timeout):
			generate_oauth_token("example.myshopify.com", "client_id_1", "client_secret_1")

	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_client_secret_never_logged(self, mock_post, mock_log):
		"""Client secret must never appear in log calls."""
		mock_post.return_value = self._mock_response()
		generate_oauth_token = self._import()

		generate_oauth_token("example.myshopify.com", "client_id_1", "SUPER_SECRET_DO_NOT_LOG")

		for call in mock_log.call_args_list:
			self.assertNotIn("SUPER_SECRET_DO_NOT_LOG", str(call))

	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_malformed_200_without_token_raises(self, mock_post, mock_log):
		"""A 200 response with valid JSON but no access_token must raise, not KeyError later."""
		mock_post.return_value = self._mock_response(json_data={"scope": "read_orders"})
		generate_oauth_token = self._import()

		with self.assertRaises(frappe.exceptions.ValidationError):
			generate_oauth_token("example.myshopify.com", "client_id_1", "client_secret_1")


class TestGetValidAccessToken(unittest.TestCase):
	"""Mock setting doc + requests — tests token cache and refresh logic."""

	def _make_setting(self, token=None, expires_at=None, client_secret="cs_test"):
		setting = MagicMock()
		setting.authentication_method = "OAuth 2.0 Client Credentials"
		setting.shopify_url = "example.myshopify.com"
		setting.client_id = "ci_test"
		setting.client_secret = None  # raw attr empty (already saved to encrypted store)
		setting.token_expires_at = expires_at
		setting.flags = frappe._dict()
		setting.get_password.side_effect = lambda field, **kw: (
			token if field == "oauth_access_token" else client_secret
		)
		setting.name = "Shopify Setting"
		return setting

	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	def test_valid_cached_token_no_http_call(self, mock_log):
		"""Fresh cached token must be returned without any HTTP request."""
		from ecommerce_integrations.shopify.oauth import get_valid_access_token

		future = now_datetime() + timedelta(hours=1)
		setting = self._make_setting(token="cached_token", expires_at=future)

		with patch("requests.post") as mock_post:
			result = get_valid_access_token(setting)

		self.assertEqual(result, "cached_token")
		mock_post.assert_not_called()

	@patch("ecommerce_integrations.shopify.oauth.set_encrypted_password")
	@patch("frappe.db.set_value")
	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_expired_token_triggers_refresh(self, mock_post, mock_log, mock_db_set, mock_encrypt):
		"""Token past expiry must trigger a new HTTP call."""
		from ecommerce_integrations.shopify.oauth import get_valid_access_token

		past = now_datetime() - timedelta(hours=1)
		setting = self._make_setting(token="old_token", expires_at=past)

		mock_resp = MagicMock()
		mock_resp.raise_for_status.return_value = None
		mock_resp.json.return_value = {"access_token": "new_token", "expires_in": 86399}
		mock_post.return_value = mock_resp
		setting.reload = MagicMock()

		result = get_valid_access_token(setting)

		mock_post.assert_called_once()
		mock_encrypt.assert_called_once()
		self.assertEqual(result, "new_token")

	@patch("ecommerce_integrations.shopify.oauth.set_encrypted_password")
	@patch("frappe.db.set_value")
	@patch("ecommerce_integrations.shopify.oauth.create_shopify_log")
	@patch("requests.post")
	def test_missing_token_triggers_fetch(self, mock_post, mock_log, mock_db_set, mock_encrypt):
		"""No cached token at all must trigger a fresh fetch."""
		from ecommerce_integrations.shopify.oauth import get_valid_access_token

		setting = self._make_setting(token=None, expires_at=None)

		mock_resp = MagicMock()
		mock_resp.raise_for_status.return_value = None
		mock_resp.json.return_value = {"access_token": "fresh_token", "expires_in": 86399}
		mock_post.return_value = mock_resp
		setting.reload = MagicMock()

		result = get_valid_access_token(setting)

		mock_post.assert_called_once()
		self.assertEqual(result, "fresh_token")

	def test_transient_refresh_is_retried_once(self):
		from ecommerce_integrations.shopify.oauth import get_valid_access_token

		setting = self._make_setting(token=None, expires_at=None)
		with (
			patch(
				"ecommerce_integrations.shopify.oauth.refresh_oauth_token",
				side_effect=[requests.exceptions.Timeout("timed out"), "fresh_token"],
			) as mock_refresh,
			patch("ecommerce_integrations.shopify.oauth.time.sleep") as mock_sleep,
			patch("ecommerce_integrations.shopify.oauth.create_shopify_log"),
		):
			result = get_valid_access_token(setting)

		self.assertEqual(result, "fresh_token")
		self.assertEqual(mock_refresh.call_count, 2)
		mock_sleep.assert_called_once_with(1)


class TestValidateRequestHmac(unittest.TestCase):
	"""HMAC validation in connection._validate_request."""

	def _make_hmac(self, secret: str, payload: bytes) -> str:
		sig = base64.b64encode(hmac.new(secret.encode("utf8"), payload, hashlib.sha256).digest())
		return sig.decode()

	def _make_mock_request(self, payload: bytes):
		req = MagicMock()
		req.data = payload
		return req

	def _make_setting(self, auth_method, shared_secret=None, client_secret=None):
		setting = MagicMock()
		setting.authentication_method = auth_method
		setting.shared_secret = shared_secret
		setting.get_password.return_value = client_secret
		return setting

	@patch("ecommerce_integrations.shopify.connection.create_shopify_log")
	@patch("frappe.get_doc")
	def test_oauth_uses_client_secret_for_hmac(self, mock_get_doc, mock_log):
		from ecommerce_integrations.shopify.connection import _validate_request

		payload = b'{"id": 123}'
		secret = "oauth_client_secret"
		mock_get_doc.return_value = self._make_setting("OAuth 2.0 Client Credentials", client_secret=secret)
		correct_hmac = self._make_hmac(secret, payload)
		req = self._make_mock_request(payload)

		# Should not raise
		_validate_request(req, correct_hmac)

	@patch("ecommerce_integrations.shopify.connection.create_shopify_log")
	@patch("frappe.get_doc")
	def test_static_token_uses_shared_secret_for_hmac(self, mock_get_doc, mock_log):
		from ecommerce_integrations.shopify.connection import _validate_request

		payload = b'{"id": 456}'
		secret = "static_shared_secret"
		mock_get_doc.return_value = self._make_setting("Static Token", shared_secret=secret)
		correct_hmac = self._make_hmac(secret, payload)
		req = self._make_mock_request(payload)

		# Should not raise
		_validate_request(req, correct_hmac)

	@patch("ecommerce_integrations.shopify.connection.create_shopify_log")
	@patch("frappe.throw")
	@patch("frappe.get_doc")
	def test_wrong_secret_fails_hmac(self, mock_get_doc, mock_throw, mock_log):
		from ecommerce_integrations.shopify.connection import _validate_request

		payload = b'{"id": 789}'
		mock_get_doc.return_value = self._make_setting(
			"OAuth 2.0 Client Credentials", client_secret="correct_secret"
		)
		wrong_hmac = self._make_hmac("wrong_secret", payload)
		req = self._make_mock_request(payload)

		_validate_request(req, wrong_hmac)

		mock_throw.assert_called_once()
		self.assertIn("Unverified", str(mock_throw.call_args))

	@patch("ecommerce_integrations.shopify.connection.create_shopify_log")
	@patch("frappe.get_doc")
	def test_missing_hmac_header_fails(self, mock_get_doc, mock_log):
		"""A request with no HMAC header must be rejected, not crash on None.encode()."""
		from ecommerce_integrations.shopify.connection import _validate_request

		mock_get_doc.return_value = self._make_setting(
			"OAuth 2.0 Client Credentials", client_secret="correct_secret"
		)
		req = self._make_mock_request(b'{"id": 1}')

		# frappe.throw raises ValidationError, halting before the None.encode() path
		with self.assertRaises(frappe.exceptions.ValidationError):
			_validate_request(req, None)


class TestStoreRequestData(unittest.TestCase):
	@patch("ecommerce_integrations.shopify.connection.process_request")
	@patch("ecommerce_integrations.shopify.connection._validate_request")
	@patch("ecommerce_integrations.shopify.connection.frappe.get_doc")
	def test_disabled_integration_ignores_remote_webhooks(self, mock_get_doc, mock_validate, mock_process):
		from ecommerce_integrations.shopify.connection import store_request_data

		setting = MagicMock()
		setting.is_enabled.return_value = False
		mock_get_doc.return_value = setting
		had_request = hasattr(frappe.local, "request")
		previous_request = getattr(frappe.local, "request", None)
		frappe.local.request = MagicMock()
		try:
			store_request_data()
		finally:
			if had_request:
				frappe.local.request = previous_request
			else:
				del frappe.local.request

		mock_validate.assert_not_called()
		mock_process.assert_not_called()


class TestShopifySettingOauthLifecycle(unittest.TestCase):
	def _make_setting(self, **values):
		from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import (
			ShopifySetting,
		)

		setting = ShopifySetting(
			{
				"doctype": "Shopify Setting",
				"enable_shopify": 1,
				"shopify_url": "test-store.myshopify.com",
				"authentication_method": "OAuth 2.0 Client Credentials",
				"client_id": "client-id",
				"client_secret": "client-secret",
				**values,
			}
		)
		setting._get_password_safe = lambda fieldname: ""
		return setting

	def test_before_save_reuses_token_minted_during_validate(self):
		setting = self._make_setting()
		setting.flags.oauth_token_refreshed_during_save = True

		with patch("ecommerce_integrations.shopify.oauth.refresh_oauth_token") as mock_refresh:
			setting.before_save()

		mock_refresh.assert_not_called()

	@patch("ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.create_shopify_log")
	@patch("ecommerce_integrations.shopify.connection.unregister_webhooks")
	def test_disable_oauth_refreshes_token_before_cleanup(self, mock_unregister, mock_log):
		setting = self._make_setting(enable_shopify=0)
		setting.webhooks = [frappe._dict(webhook_id="1")]
		setting._get_or_generate_oauth_token = MagicMock(return_value="fresh-token")

		setting._handle_webhooks()

		setting._get_or_generate_oauth_token.assert_called_once()
		mock_unregister.assert_called_once_with(setting.shopify_url, "fresh-token")
		self.assertEqual(setting.webhooks, [])

	@patch("ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.create_shopify_log")
	@patch("ecommerce_integrations.shopify.connection.unregister_webhooks")
	def test_remote_cleanup_failure_does_not_block_disable(self, mock_unregister, mock_log):
		setting = self._make_setting(enable_shopify=0)
		setting.webhooks = [frappe._dict(webhook_id="1")]
		setting._get_or_generate_oauth_token = MagicMock(
			side_effect=requests.exceptions.Timeout("Shopify unavailable")
		)

		setting._handle_webhooks()

		mock_unregister.assert_not_called()
		mock_log.assert_called_once()
		self.assertEqual(setting.webhooks, [])

	def test_oauth_method_marks_token_as_server_managed(self):
		# _save_passwords deletes Password fields whose in-memory value is empty;
		# the OAuth token only lives in __Auth, so validate() must shield it.
		setting = self._make_setting()

		setting._preserve_managed_oauth_token()

		self.assertEqual(setting.flags.ignore_save_passwords, ["oauth_access_token"])

	def test_static_method_leaves_token_unshielded(self):
		setting = self._make_setting(authentication_method="Static Token")

		setting._preserve_managed_oauth_token()

		self.assertNotIn("oauth_access_token", setting.flags.ignore_save_passwords or [])
