# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

"""
OAuth 2.0 Client Credentials Flow for Shopify Apps

This flow exchanges a Dev Dashboard Client ID and Client Secret for a
short-lived token. Shopify supports it only when the app and target shop
belong to the same Shopify organization.

Reference:
  https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/client-credentials-grant
"""

import json
import re
import time
from datetime import timedelta
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_datetime, get_datetime_str, now_datetime
from frappe.utils.password import set_encrypted_password

from ecommerce_integrations.shopify.constants import AUTH_METHOD_OAUTH, SETTING_DOCTYPE

SHOPIFY_HOST_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")


def normalize_shopify_url(shopify_url: str) -> str:
	"""Return a normalized myshopify.com hostname and reject unsafe URLs."""
	value = (shopify_url or "").strip()
	parsed = urlsplit(value if "://" in value else f"https://{value}")

	try:
		port = parsed.port
	except ValueError:
		port = -1

	hostname = (parsed.hostname or "").lower()
	if (
		parsed.scheme not in {"http", "https"}
		or parsed.username
		or parsed.password
		or port is not None
		or parsed.path not in {"", "/"}
		or parsed.query
		or parsed.fragment
		or not SHOPIFY_HOST_PATTERN.fullmatch(hostname)
	):
		frappe.throw(
			_("Shop URL must be a valid Shopify hostname such as store-name.myshopify.com."),
			title=_("Invalid Shopify URL"),
		)

	return hostname


def get_oauth_token_endpoint(shopify_url: str) -> str:
	"""
	Construct the OAuth token endpoint URL for a given shop.

	Args:
	    shopify_url: e.g. 'example.myshopify.com'

	Returns:
	    'https://example.myshopify.com/admin/oauth/access_token'
	"""
	shop_url = normalize_shopify_url(shopify_url)
	return f"https://{shop_url}/admin/oauth/access_token"


def generate_oauth_token(shopify_url: str, client_id: str, client_secret: str) -> dict:
	"""
	Generate a new OAuth 2.0 access token using Client Credentials Grant.

	POST https://{shop}.myshopify.com/admin/oauth/access_token
	  Content-Type: application/x-www-form-urlencoded
	  grant_type=client_credentials&client_id=...&client_secret=...

	Returns:
	    dict with keys: access_token, expires_in (86399 = ~24h), scope

	Raises:
	    frappe.ValidationError: If token generation fails
	"""
	token_endpoint = get_oauth_token_endpoint(shopify_url)

	payload = {
		"grant_type": "client_credentials",
		"client_id": client_id,
		"client_secret": client_secret,
	}

	headers = {
		"Content-Type": "application/x-www-form-urlencoded",
	}

	try:
		response = requests.post(token_endpoint, data=payload, headers=headers, timeout=30)
		response.raise_for_status()

		token_data = response.json()

		if not token_data.get("access_token"):
			frappe.throw(
				_("Shopify returned a response without an access token"),
				title=_("OAuth Authentication Error"),
			)

		# no success log: create_shopify_log commits, which would split a background job's transaction
		return token_data

	except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
		raise
	except requests.exceptions.RequestException as e:
		error_message = str(e)
		error_code = None
		error_response = None

		if hasattr(e, "response") and e.response is not None:
			try:
				error_response = e.response.json()
				error_code = error_response.get("error")
				error_message = error_response.get("error_description", error_code or str(e))
			except json.JSONDecodeError:
				error_message = e.response.text or str(e)

		if error_code == "shop_not_permitted":
			error_message = _(
				"{0} Client credentials work only when the app and shop belong to the same"
				" Shopify organization. For an external merchant store, use Shopify's"
				" authorization code or token exchange flow instead."
			).format(error_message)

		# Never log the actual client_secret
		sanitized_payload = payload.copy()
		sanitized_payload["client_secret"] = "***REDACTED***"

		# defer_insert: a token refresh runs inside callers' transactions (e.g. order import),
		# which create_shopify_log would commit
		frappe.log_error(
			title=_("Shopify OAuth token request failed"),
			message=json.dumps(
				{"error": error_message, "request": sanitized_payload, "response": error_response},
				indent=4,
				default=str,
			),
			defer_insert=True,
		)

		frappe.throw(
			_("Failed to generate OAuth token: {0}").format(error_message),
			title=_("OAuth Authentication Error"),
		)


def is_token_valid(token_expires_at, buffer_minutes: int = 5) -> bool:
	"""
	Check if the OAuth token is still valid with a safety buffer.

	Returns False if token_expires_at is empty or within buffer_minutes of expiry.
	The 5-minute default prevents mid-request token expiry.
	"""
	if not token_expires_at:
		return False

	expiry_datetime = get_datetime(token_expires_at)
	buffer_time = now_datetime() + timedelta(minutes=buffer_minutes)

	return expiry_datetime > buffer_time


def calculate_token_expiry(expires_in_seconds: int):
	"""Convert Shopify's expires_in (seconds) into an absolute datetime."""
	return now_datetime() + timedelta(seconds=expires_in_seconds)


def refresh_oauth_token(setting, *, persist: bool = True) -> str:
	"""Generate a fresh OAuth token and set it on `setting`.

	persist=False is for the setting's own save: the token is then stored only if that
	save succeeds, since create_shopify_log commits mid-save.
	"""
	if setting.authentication_method != AUTH_METHOD_OAUTH:
		frappe.throw(
			_("Token refresh is only applicable for OAuth 2.0 authentication"),
			title=_("Invalid Authentication Method"),
		)

	client_secret = setting.get_password("client_secret", raise_exception=False)
	if not client_secret:
		frappe.throw(
			_("Client Secret is missing. Please re-enter and save."),
			title=_("OAuth Authentication Error"),
		)

	token_data = generate_oauth_token(
		setting.shopify_url,
		setting.client_id,
		client_secret,
	)

	token = token_data["access_token"]
	expires_at = get_datetime_str(calculate_token_expiry(cint(token_data.get("expires_in")) or 86399))

	setting.oauth_access_token = token
	setting.token_expires_at = expires_at
	# _handle_webhooks can refresh during validate. before_save must reuse it.
	setting.flags.oauth_token_refreshed_during_save = True

	if persist:
		set_encrypted_password(SETTING_DOCTYPE, setting.name, token, fieldname="oauth_access_token")
		frappe.db.set_value(
			SETTING_DOCTYPE, setting.name, "token_expires_at", expires_at, update_modified=False
		)

	return token


def get_valid_access_token(setting, *, persist: bool = True) -> str:
	"""
	Return a valid OAuth access token, auto-refreshing if needed.

	This is the main entry point — called by connection.py on every API request.
	"""
	if setting.authentication_method != AUTH_METHOD_OAUTH:
		frappe.throw(
			_("This method is only for OAuth 2.0 authentication"),
			title=_("Invalid Authentication Method"),
		)

	if is_token_valid(setting.token_expires_at):
		current_token = setting.get_password("oauth_access_token", raise_exception=False)
		if current_token:
			return current_token

	try:
		return refresh_oauth_token(setting, persist=persist)
	except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
		# file log, not create_shopify_log: that would commit the caller's transaction
		frappe.logger("shopify").warning(f"OAuth token refresh failed, retrying once: {e}")
		time.sleep(1)
		return refresh_oauth_token(setting, persist=persist)
