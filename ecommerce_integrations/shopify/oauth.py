# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

"""
OAuth 2.0 Client Credentials Flow for Shopify Apps.

Implements token generation and refresh for apps created via the Shopify
Dev Dashboard. Required for all custom apps created on or after Jan 1, 2026.

Operates per Shopify Account document (multi-tenant).
"""

import json
import time
from datetime import datetime, timedelta

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, get_datetime_str, now_datetime
from frappe.utils.password import set_encrypted_password

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE
from ecommerce_integrations.shopify.utils import create_shopify_log


def get_oauth_token_endpoint(shopify_url: str) -> str:
	shop_url = shopify_url.replace("https://", "").replace("http://", "")
	return f"https://{shop_url}/admin/oauth/access_token"


def generate_oauth_token(shopify_url: str, client_id: str, client_secret: str) -> dict:
	"""POST client_credentials to Shopify and return the token payload."""
	token_endpoint = get_oauth_token_endpoint(shopify_url)

	payload = {
		"grant_type": "client_credentials",
		"client_id": client_id,
		"client_secret": client_secret,
	}
	headers = {"Content-Type": "application/x-www-form-urlencoded"}

	try:
		response = requests.post(token_endpoint, data=payload, headers=headers, timeout=30)
		response.raise_for_status()
		token_data = response.json()

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
				error_message = error_response.get("error_description", error_response.get("error", str(e)))
			except json.JSONDecodeError:
				error_message = e.response.text or str(e)

		sanitized_payload = payload.copy()
		sanitized_payload["client_secret"] = "REDACTED"

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
	if not token_expires_at:
		return False
	expiry_datetime = get_datetime(token_expires_at)
	buffer_time = now_datetime() + timedelta(minutes=buffer_minutes)
	return expiry_datetime > buffer_time


def calculate_token_expiry(expires_in_seconds: int) -> datetime:
	return now_datetime() + timedelta(seconds=expires_in_seconds)


def refresh_oauth_token(setting) -> str:
	"""Generate a fresh token for the given Shopify Account and persist it."""
	if setting.authentication_method != "OAuth 2.0 Client Credentials":
		frappe.throw(
			_("Token refresh is only applicable for OAuth 2.0 authentication"),
			title=_("Invalid Authentication Method"),
		)

	setting.reload()

	token_data = generate_oauth_token(
		setting.shopify_url,
		setting.client_id,
		setting.get_password("client_secret"),
	)

	expires_at = calculate_token_expiry(token_data.get("expires_in", 86399))

	set_encrypted_password(
		ACCOUNT_DOCTYPE,
		setting.name,
		token_data["access_token"],
		fieldname="oauth_access_token",
	)

	frappe.db.set_value(
		ACCOUNT_DOCTYPE,
		setting.name,
		"token_expires_at",
		get_datetime_str(expires_at),
		update_modified=False,
	)

	setting.reload()
	return token_data["access_token"]


def get_valid_access_token(setting) -> str:
	"""Return a valid OAuth access token for the given Shopify Account, refreshing if needed."""
	if setting.authentication_method != "OAuth 2.0 Client Credentials":
		frappe.throw(
			_("This method is only for OAuth 2.0 authentication"),
			title=_("Invalid Authentication Method"),
		)

	if is_token_valid(setting.token_expires_at):
		current_token = setting.get_password("oauth_access_token", raise_exception=False)
		if current_token:
			return current_token

	try:
		return refresh_oauth_token(setting)
	except Exception as e:
		create_shopify_log(
			status="Warning",
			method="ecommerce_integrations.shopify.oauth.get_valid_access_token",
			message=_("Token refresh failed, retrying once..."),
			exception=str(e),
		)
		time.sleep(1)
		return refresh_oauth_token(setting)


def validate_oauth_credentials(shopify_url: str, client_id: str, client_secret: str) -> bool:
	"""Verify credentials by attempting a token generation. Re-raises on failure."""
	token_data = generate_oauth_token(shopify_url, client_id, client_secret)
	return bool(token_data.get("access_token"))
