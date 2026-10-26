import base64
import functools
import hashlib
import hmac
import json
from contextlib import contextmanager

import frappe
from frappe import _
from pyactiveresource.connection import ResourceNotFound
from shopify.resources import Webhook
from shopify.session import Session

from ecommerce_integrations.shopify.constants import (
	API_VERSION,
	AUTH_METHOD_OAUTH,
	EVENT_MAPPER,
	WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.oauth import get_valid_access_token
from ecommerce_integrations.shopify.utils import (
	create_shopify_log,
	get_account_name,
	get_default_account,
	get_enabled_accounts,
	get_shopify_account,
)


def temp_shopify_session(func):
	"""Any function that needs to access shopify api needs this decorator. The decorator starts a temp session that's destroyed when function returns.

	The session belongs to the account passed as the `shopify_account` keyword argument
	(document or name), or to the only enabled account when the caller passes none.
	"""

	@functools.wraps(func)
	def wrapper(*args, **kwargs):
		# no auth in testing
		if frappe.flags.in_test:
			return func(*args, **kwargs)

		account = kwargs.get("shopify_account")
		setting = get_shopify_account(account) if account else get_default_account()
		if setting.is_enabled():
			with shopify_session(setting):
				return func(*args, **kwargs)

	return wrapper


@contextmanager
def shopify_session(shopify_account):
	"""Temporary Shopify API session for one account, for code that holds the account itself."""
	# no auth in testing
	if frappe.flags.in_test:
		yield
		return

	setting = get_shopify_account(shopify_account)
	with Session.temp(setting.shopify_url, API_VERSION, _get_access_token(setting)):
		yield


def _get_access_token(setting) -> str:
	if setting.authentication_method == AUTH_METHOD_OAUTH:
		return get_valid_access_token(setting)

	token = setting.get_password("password", raise_exception=False)
	if not token:
		frappe.throw(_("Shopify access token is not configured"), title=_("Authentication Error"))
	return token


def register_webhooks(shopify_url: str, password: str) -> list[Webhook]:
	"""Register required webhooks with shopify and return registered webhooks."""
	new_webhooks = []

	# clear all stale webhooks matching current site url before registering new ones
	unregister_webhooks(shopify_url, password)

	with Session.temp(shopify_url, API_VERSION, password):
		for topic in WEBHOOK_EVENTS:
			webhook = Webhook.create({"topic": topic, "address": get_callback_url(), "format": "json"})

			if webhook.is_valid():
				new_webhooks.append(webhook)
			else:
				create_shopify_log(
					status="Error",
					response_data=webhook.to_dict(),
					exception=webhook.errors.full_messages(),
				)

	return new_webhooks


def unregister_webhooks(shopify_url: str, password: str) -> None:
	"""Unregister all webhooks from shopify that correspond to current site url."""
	url = get_current_domain_name()

	with Session.temp(shopify_url, API_VERSION, password):
		for webhook in Webhook.find():
			if url in webhook.address:
				webhook.destroy()


def delete_webhooks(shopify_url: str, password: str, webhook_ids: list[str]) -> None:
	"""Delete exactly these webhooks; ones already removed are ignored."""
	with Session.temp(shopify_url, API_VERSION, password):
		for webhook_id in webhook_ids:
			try:
				Webhook({"id": webhook_id}).destroy()
			except ResourceNotFound:
				pass


def get_current_domain_name() -> str:
	"""Get current site domain name. E.g. test.erpnext.com

	If developer_mode is enabled and localtunnel_url is set in site config then domain  is set to localtunnel_url.
	"""
	if frappe.conf.developer_mode and frappe.conf.localtunnel_url:
		return frappe.conf.localtunnel_url
	else:
		return frappe.request.host


def get_callback_url() -> str:
	"""Shopify calls this url when new events occur to subscribed webhooks.

	If developer_mode is enabled and localtunnel_url is set in site config then callback url is set to localtunnel_url.
	"""
	url = get_current_domain_name()

	return f"https://{url}/api/method/ecommerce_integrations.shopify.connection.store_request_data"


# Shopify calls this webhook endpoint without a session; every request is verified with
# the HMAC signature of the store it claims to come from before anything is processed.
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
def store_request_data() -> None:
	"""Validate and enqueue webhooks of the account they belong to."""
	if frappe.request:
		hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")

		shopify_account = _get_webhook_account(frappe.request)
		if not shopify_account:
			return
		_validate_request(frappe.request, hmac_header, shopify_account)

		data = json.loads(frappe.request.data)
		event = frappe.request.headers.get("X-Shopify-Topic")

		process_request(data, event, shopify_account)


def _get_webhook_account(req):
	"""The account an incoming webhook belongs to.

	Webhooks are accepted while exactly one account is enabled; with more, the request
	cannot be attributed to a store and is logged and refused. While no account is enabled,
	deliveries are ignored.
	"""
	if not get_enabled_accounts():
		return None

	try:
		return get_default_account()
	except frappe.ValidationError as e:
		create_shopify_log(status="Error", request_data=frappe.safe_decode(req.data), exception=e)
		raise


def process_request(data, event, shopify_account=None):
	# create log
	log = create_shopify_log(method=EVENT_MAPPER[event], request_data=data, shopify_account=shopify_account)

	# enqueue backround job
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{
			"payload": data,
			"request_id": log.name,
			"shopify_account": get_account_name(shopify_account),
		},
	)


def get_webhook_secret(setting) -> str | None:
	"""Secret Shopify signs the account's webhooks with: an OAuth app signs with its client secret."""
	if setting.authentication_method == AUTH_METHOD_OAUTH:
		return setting.get_password("client_secret", raise_exception=False)
	return setting.shared_secret


def _validate_request(req, hmac_header, shopify_account):
	"""Verify the raw webhook body with the account's signing secret."""
	secret_key = get_webhook_secret(get_shopify_account(shopify_account))
	request_data = frappe.safe_decode(req.data)

	if not secret_key:
		create_shopify_log(
			status="Error",
			request_data=request_data,
			exception="Secret key not configured",
			shopify_account=shopify_account,
		)
		frappe.throw(_("Webhook validation failed: Secret key not configured"))

	if not hmac_header:
		create_shopify_log(
			status="Error",
			request_data=request_data,
			exception="Missing HMAC header",
			shopify_account=shopify_account,
		)
		frappe.throw(_("Unverified Webhook Data"))

	sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

	# Timing-safe comparison to prevent signature timing attacks
	if not hmac.compare_digest(sig, hmac_header.encode()):
		create_shopify_log(status="Error", request_data=request_data, shopify_account=shopify_account)
		frappe.throw(_("Unverified Webhook Data"))
