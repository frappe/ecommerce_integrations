# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

"""
OAuth 2.0 Client Credentials Flow for Shopify Apps (post Jan 1, 2026)

Apps created via Shopify Dev Dashboard use Client ID + Client Secret
to obtain short-lived (24h) access tokens, instead of the old static
Access Token model.

Reference:
  https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/client-credentials-grant
"""

import json
import time
from datetime import timedelta

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, get_datetime_str, now_datetime
from frappe.utils.password import set_encrypted_password

from ecommerce_integrations.shopify.utils import create_shopify_log


def get_oauth_token_endpoint(shopify_url: str) -> str:
	"""
	Construct the OAuth token endpoint URL for a given shop.

	Args:
	    shopify_url: e.g. 'example.myshopify.com'

	Returns:
	    'https://example.myshopify.com/admin/oauth/access_token'
	"""
	shop_url = shopify_url.replace("https://", "").replace("http://", "").rstrip("/")
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

		# Guard a malformed 200 (valid JSON but no token) so callers don't KeyError later
		if not token_data.get("access_token"):
			frappe.throw(
				_("Shopify returned a response without an access token"),
				title=_("OAuth Authentication Error"),
			)

		create_shopify_log(
			status="Success",
			method="ecommerce_integrations.shopify.oauth.generate_oauth_token",
			message=_("OAuth token generated successfully"),
		)

		return token_data

	except requests.exceptions.RequestException as e:
		error_message = str(e)
		error_response = None

		if hasattr(e, "response") and e.response is not None:
			try:
				error_response = e.response.json()
				error_message = error_response.get(
					"error_description", error_response.get("error", str(e))
				)
			except json.JSONDecodeError:
				error_message = e.response.text or str(e)

		# Never log the actual client_secret
		sanitized_payload = payload.copy()
		sanitized_payload["client_secret"] = "***REDACTED***"

		create_shopify_log(
			status="Error",
			method="ecommerce_integrations.shopify.oauth.generate_oauth_token",
			message=_("Failed to generate OAuth token"),
			exception=error_message,
			request_data=sanitized_payload,
			response_data=error_response,
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


def refresh_oauth_token(setting, client_secret=None) -> str:
	"""
	Generate a fresh OAuth token and persist it encrypted in Shopify Setting.

	Args:
	    setting: ShopifySetting document instance
	    client_secret: Optional — pass directly during save cycle when
	                   the password hasn't been committed to DB yet.

	Returns:
	    The new access token string
	"""
	if setting.authentication_method != "OAuth 2.0 Client Credentials":
		frappe.throw(
			_("Token refresh is only applicable for OAuth 2.0 authentication"),
			title=_("Invalid Authentication Method"),
		)

	# If client_secret not passed, read from DB (raise_exception=False so the
	# guard below produces a clean message instead of an AuthenticationError)
	if not client_secret:
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

	# Shopify may send expires_in as null or a string; coerce defensively
	try:
		expires_in = int(token_data.get("expires_in") or 86399)
	except (TypeError, ValueError):
		expires_in = 86399
	expires_at = calculate_token_expiry(expires_in)

	# Store token encrypted (same mechanism as the password field)
	set_encrypted_password(
		"Shopify Setting",
		setting.name,
		token_data["access_token"],
		fieldname="oauth_access_token",
	)

	frappe.db.set_value(
		"Shopify Setting",
		setting.name,
		"token_expires_at",
		get_datetime_str(expires_at),
		update_modified=False,
	)

	# Sync only the field we wrote — full reload() would discard in-memory changes
	# made earlier in the same validate() call (e.g. the shopify_url https-strip)
	setting.token_expires_at = get_datetime_str(expires_at)

	return token_data["access_token"]


def get_valid_access_token(setting) -> str:
	"""
	Return a valid OAuth access token, auto-refreshing if needed.

	This is the main entry point — called by connection.py on every API request.
	"""
	if setting.authentication_method != "OAuth 2.0 Client Credentials":
		frappe.throw(
			_("This method is only for OAuth 2.0 authentication"),
			title=_("Invalid Authentication Method"),
		)

	# Fast path: cached token is still valid
	if is_token_valid(setting.token_expires_at):
		current_token = setting.get_password("oauth_access_token", raise_exception=False)
		if current_token:
			return current_token

	# Token missing or expired — refresh, with a single retry for transient network errors only.
	# Permanent errors (bad credentials, missing secret) are NOT retried.
	try:
		return refresh_oauth_token(setting)
	except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
		create_shopify_log(
			status="Warning",
			method="ecommerce_integrations.shopify.oauth.get_valid_access_token",
			message=_("Token refresh failed due to network error, retrying once..."),
			exception=str(e),
		)
		time.sleep(1)
		return refresh_oauth_token(setting)  # Let this raise if it still fails
