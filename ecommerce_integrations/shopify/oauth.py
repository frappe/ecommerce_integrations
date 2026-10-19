# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

"""OAuth 2.0 client credentials grant for Shopify apps, per Shopify Account.

Apps created in the Shopify Dev Dashboard no longer get a static Admin API token. They
authenticate with a client ID and secret and exchange them for an access token that
expires after about 24 hours. The token is stored encrypted on the account and renewed
shortly before it expires.
"""

import json
import time
from datetime import timedelta

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, get_datetime_str, now_datetime
from frappe.utils.password import set_encrypted_password

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, OAUTH_AUTH_METHOD
from ecommerce_integrations.shopify.utils import normalize_shop_url

# renew this long before the token expires, so a request never starts with a stale token
EXPIRY_BUFFER = timedelta(minutes=5)
DEFAULT_TOKEN_LIFETIME = 86399  # seconds, what Shopify issues


def generate_oauth_token(shopify_url: str, client_id: str, client_secret: str) -> dict:
	"""Exchange client ID and secret for an access token; returns Shopify's token payload."""
	payload = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}

	try:
		response = requests.post(
			f"https://{normalize_shop_url(shopify_url)}/admin/oauth/access_token",
			data=payload,
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=30,
		)
		response.raise_for_status()
		return response.json()

	except requests.exceptions.RequestException as e:
		error_message, error_response = str(e), None
		if e.response is not None:
			try:
				error_response = e.response.json()
				error_message = (
					error_response.get("error_description") or error_response.get("error") or error_message
				)
			except ValueError:
				error_message = e.response.text or error_message

		# Not create_shopify_log: it commits, and a token can be requested inside another
		# document's transaction, e.g. while an Item is uploaded on save.
		frappe.log_error(
			title=_("Shopify: OAuth token request for {0} failed").format(shopify_url),
			message=json.dumps(
				{
					"error": error_message,
					"request": {**payload, "client_secret": "REDACTED"},
					"response": error_response,
				},
				indent=1,
				default=str,
			),
		)
		frappe.throw(
			_("Failed to generate OAuth token: {0}").format(error_message),
			title=_("OAuth Authentication Error"),
		)


def is_token_valid(token_expires_at) -> bool:
	return bool(token_expires_at) and get_datetime(token_expires_at) > now_datetime() + EXPIRY_BUFFER


def refresh_oauth_token(setting) -> str:
	"""Generate a new token for the account and store it, if the account is saved already.

	The account document itself is only updated in memory: this also runs while the
	account is being saved, and reloading it would drop the changes being saved.
	"""
	token_data = generate_oauth_token(
		setting.shopify_url, setting.client_id, setting.get_password("client_secret")
	)
	expires_at = get_datetime_str(
		now_datetime() + timedelta(seconds=token_data.get("expires_in") or DEFAULT_TOKEN_LIFETIME)
	)

	if not setting.is_new() and frappe.db.exists(ACCOUNT_DOCTYPE, setting.name):
		set_encrypted_password(
			ACCOUNT_DOCTYPE, setting.name, token_data["access_token"], fieldname="oauth_access_token"
		)
		frappe.db.set_value(
			ACCOUNT_DOCTYPE, setting.name, "token_expires_at", expires_at, update_modified=False
		)

	# keep the token on the document too: a new account is saved with it
	setting.oauth_access_token = token_data["access_token"]
	setting.token_expires_at = expires_at
	return token_data["access_token"]


def get_valid_access_token(setting) -> str:
	"""The account's access token, renewed when it is missing or about to expire."""
	if setting.authentication_method != OAUTH_AUTH_METHOD:
		frappe.throw(_("Shopify Account {0} does not use OAuth 2.0.").format(setting.name))

	if is_token_valid(setting.token_expires_at):
		token = setting.get_password("oauth_access_token", raise_exception=False)
		if token:
			return token

	try:
		return refresh_oauth_token(setting)
	except Exception:
		# one retry for a transient failure; a second failure is logged and raised
		time.sleep(1)
		return refresh_oauth_token(setting)
