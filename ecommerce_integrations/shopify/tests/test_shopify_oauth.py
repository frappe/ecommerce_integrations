# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import base64
import hashlib
import hmac
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe
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


class TestValidateRequestHmac(unittest.TestCase):
	"""HMAC validation in connection._validate_request."""

	def _make_hmac(self, secret: str, payload: bytes) -> str:
		sig = base64.b64encode(
			hmac.new(secret.encode("utf8"), payload, hashlib.sha256).digest()
		)
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
		mock_get_doc.return_value = self._make_setting(
			"OAuth 2.0 Client Credentials", client_secret=secret
		)
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
		mock_get_doc.return_value = self._make_setting(
			"Static Token", shared_secret=secret
		)
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
