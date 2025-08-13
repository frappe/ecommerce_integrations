import base64
import functools
import hashlib
import hmac
import json

import frappe
from frappe import _
from shopify.resources import Webhook
from shopify.session import Session

from ecommerce_integrations.shopify.constants import (
	API_VERSION,
	EVENT_MAPPER,
	SETTING_DOCTYPE,
	WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def temp_shopify_session(func=None, *, account=None):
	"""Enhanced decorator with account context support."""
	def decorator(f):
		@functools.wraps(f)
		def wrapper(*args, **kwargs):
			# Extract account from kwargs if not provided
			account_param = account or kwargs.get('account')
			
			if account_param:
				from ecommerce_integrations.shopify.utils import resolve_account_context
				account_doc = resolve_account_context(account_param)
				shopify_url = account_doc.get_shop_url()
				password = account_doc.get_access_token()
			else:
				# Use standardized legacy fallback
				from ecommerce_integrations.shopify.utils import resolve_account_context
				setting = resolve_account_context(None)  # Gets legacy setting
				shopify_url = setting.shopify_url
				password = setting.get_password("password")
			
			with Session.temp(shopify_url, API_VERSION, password):
				return f(*args, **kwargs)
				
		return wrapper
	
	if func is None:
		return decorator
	else:
		return decorator(func)


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


@frappe.whitelist(allow_guest=True)
def store_request_data() -> None:
	if frappe.request:
		hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")
		shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")

		# Resolve account by shop domain
		account = _get_account_by_domain(shop_domain)
		if not account:
			create_shopify_log(
				status="Error", 
				request_data=frappe.request.data,
				exception=f"No enabled Shopify Account found for domain: {shop_domain}"
			)
			frappe.throw(_("No enabled Shopify Account found for domain: {0}").format(shop_domain))

		_validate_request(frappe.request, hmac_header, account)

		data = json.loads(frappe.request.data)
		event = frappe.request.headers.get("X-Shopify-Topic")

		process_request(data, event, account)


def process_request(data, event, account):
	# create log with account context
	log = create_shopify_log(
		method=EVENT_MAPPER[event], 
		request_data=data,
		reference_document=account.name
	)

	# enqueue background job with account context
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name, "account": account.name},
	)


def _validate_request(req, hmac_header, account):
	"""Validate webhook request using account-specific shared secret."""
	secret_key = account.get_shared_secret()

	sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

	if sig != bytes(hmac_header.encode()):
		create_shopify_log(
			status="Error", 
			request_data=req.data,
			reference_document=account.name,
			exception="Invalid HMAC signature"
		)
		frappe.throw(_("Unverified Webhook Data"))


def _get_account_by_domain(shop_domain):
	"""Get enabled Shopify Account by shop domain."""
	if not shop_domain:
		return None
	
	try:
		return frappe.get_doc("Shopify Account", {"shop_domain": shop_domain, "enabled": 1})
	except frappe.DoesNotExistError:
		return None
