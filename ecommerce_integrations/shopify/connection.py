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
	ACCOUNT_DOCTYPE,
	WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log, get_user_shopify_account


def temp_shopify_session(shopify_account=None):
    """Decorator for functions that need a temporary Shopify session."""
    print("temp_shopify_session called with ", shopify_account)

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # no auth in testing
            if frappe.flags.in_test:
                return func(*args, **kwargs)

            # If a callable is passed, call it with self to get the account
            if shopify_account is None:
                # TODO: handle if get_user_shopify_account returns None
                account = get_user_shopify_account().name
            else:
                account = shopify_account(args[0]) if callable(shopify_account) else shopify_account

            setting = frappe.get_doc(ACCOUNT_DOCTYPE, account)
            if setting.is_enabled():
                auth_details = (setting.shopify_url, API_VERSION, setting.get_password("password"))
                with Session.temp(*auth_details):
                    return func(*args, **kwargs)

        return wrapper

    return decorator


def get_auth_details(setting) -> tuple[str, str, str]:
    """Get authentication details for Shopify API."""
    # setting = frappe.get_doc(ACCOUNT_DOCTYPE, setting)
    return setting.shopify_url, API_VERSION, setting.get_password("password")


def get_temp_session_context(setting):
	"""Get a temporary Shopify session context manager."""
	auth_details = get_auth_details(setting)
	return Session.temp(*auth_details)


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
def store_request_data(**kwargs) -> None:
	if frappe.request:
		hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")
		# Get shopify account
		shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")
		settings = frappe.get_doc(ACCOUNT_DOCTYPE, shop_domain)

		_validate_request(frappe.request, hmac_header, secret_key=settings.shared_secret)

		data = json.loads(frappe.request.data)
		event = frappe.request.headers.get("X-Shopify-Topic")

		process_request(data, event, shopify_account=settings)


def process_request(data, event, shopify_account=None):
	print("Processing webhook event: ", event, "\n", shopify_account)
	# create log
	log = create_shopify_log(method=EVENT_MAPPER[event], request_data=data, shopify_account=shopify_account.name)
	print("log created")
	# enqueue background job
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name, "shopify_account": shopify_account},
	)


def _validate_request(req, hmac_header, secret_key):
	sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

	if sig != bytes(hmac_header.encode()):
		create_shopify_log(status="Error", request_data=req.data)
		frappe.throw(_("Unverified Webhook Data"))
