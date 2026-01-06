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


def temp_shopify_session(func):
	"""Any function that needs to access shopify api needs this decorator. The decorator starts a temp session that's destroyed when function returns.
	
	Store-aware: Checks for store_name in kwargs or frappe.local.shopify_store_name to determine which store's credentials to use.
	"""

	@functools.wraps(func)
	def wrapper(*args, **kwargs):
		# no auth in testing
		if frappe.flags.in_test:
			return func(*args, **kwargs)

		setting = frappe.get_doc(SETTING_DOCTYPE)
		if setting.is_enabled():
			# Determine which store's credentials to use
			store_name = kwargs.get('store_name') or getattr(frappe.local, 'shopify_store_name', None)
			
			# Use Store 2 credentials if specified and enabled
			if store_name and store_name != "Store 1" and setting.enable_store_2:
				auth_details = (setting.shopify_url_2, API_VERSION, setting.get_password("password_2"))
				frappe.logger().info(f"Using Store 2 ({store_name}) API credentials")
			else:
				# Default to Store 1
				auth_details = (setting.shopify_url, API_VERSION, setting.get_password("password"))
				if store_name:
					frappe.logger().info(f"Using Store 1 API credentials")

			with Session.temp(*auth_details):
				return func(*args, **kwargs)

	return wrapper


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
		# Detect which store sent this webhook
		shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")
		
		if not shop_domain:
			frappe.log_error(
				title="Shopify Webhook - Missing Shop Domain",
				message="X-Shopify-Shop-Domain header not found"
			)
			frappe.throw(_("Missing shop domain header"))
		
		# Get settings
		setting = frappe.get_doc(SETTING_DOCTYPE)
		
		# Determine store and credentials
		store_name = None
		shared_secret = None
		
		# Check Store 1
		if setting.shopify_url and shop_domain in setting.shopify_url:
			store_name = "Store 1"
			shared_secret = setting.shared_secret
			frappe.logger().info(f"Webhook from Store 1: {shop_domain}")
		
		# Check Store 2
		elif setting.enable_store_2 and setting.shopify_url_2 and shop_domain in setting.shopify_url_2:
			store_name = setting.store_2_name or "Store 2"
			shared_secret = setting.shared_secret_2
			frappe.logger().info(f"Webhook from Store 2 ({store_name}): {shop_domain}")
		
		# Unknown store
		else:
			frappe.log_error(
				title="Shopify Webhook - Unknown Store",
				message=f"Shop domain '{shop_domain}' does not match configured stores"
			)
			frappe.throw(_(f"Unknown Shopify store: {shop_domain}"))
		
		# Validate with correct secret
		hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")
		_validate_request(frappe.request, hmac_header, shared_secret)

		data = json.loads(frappe.request.data)
		event = frappe.request.headers.get("X-Shopify-Topic")
		
		# Set store context for API sessions
		frappe.local.shopify_store_name = store_name

		process_request(data, event, store_name)


def process_request(data, event, store_name=None):
	"""Process webhook request with store context.
	
	Args:
		data: The webhook payload data
		event: The event type (e.g., 'orders/create')
		store_name: Name of the store that sent the webhook (default: None for backward compatibility)
	"""
	# Log which store is processing
	if store_name:
		frappe.logger().info(f"Processing {event} from {store_name}")
	
	# create log
	log = create_shopify_log(method=EVENT_MAPPER[event], request_data=data)

	# enqueue backround job with store context
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name, "store_name": store_name},
	)


def _validate_request(req, hmac_header, shared_secret):
	"""Validate Shopify webhook using HMAC.
	
	Args:
		req: The request object
		hmac_header: The HMAC header from request
		shared_secret: Shared secret for the specific store
	"""
	sig = base64.b64encode(hmac.new(shared_secret.encode("utf8"), req.data, hashlib.sha256).digest())

	if sig != bytes(hmac_header.encode()):
		create_shopify_log(status="Error", request_data=req.data)
		frappe.throw(_("Unverified Webhook Data"))
