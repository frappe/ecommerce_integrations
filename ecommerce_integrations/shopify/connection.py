import base64
import functools
import inspect
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
from ecommerce_integrations.shopify.utils import (
	create_shopify_log,
	get_shopify_setting_doc,
	get_shopify_setting_name_by_domain,
)


def temp_shopify_session(func):
	"""Any function that needs to access shopify api needs this decorator. The decorator starts a temp session that's destroyed when function returns."""

	signature = inspect.signature(func)
	accepts_setting_kw = any(
		param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
		and param.name == "shopify_setting"
		for param in signature.parameters.values()
	) or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())

	@functools.wraps(func)
	def wrapper(*args, **kwargs):
		# no auth in testing
		if frappe.flags.in_test:
			return func(*args, **kwargs)

		setting_ref = kwargs.get("shopify_setting")
		if not accepts_setting_kw and "shopify_setting" in kwargs:
			setting_ref = kwargs.pop("shopify_setting")
		if not setting_ref and args:
			first_arg = args[0]
			if getattr(first_arg, "doctype", None) == SETTING_DOCTYPE:
				setting_ref = first_arg

		if not setting_ref:
			setting_ref = getattr(frappe.flags, "shopify_setting", None)

		setting = get_shopify_setting_doc(setting_ref, require_enabled=True)

		if setting.is_enabled():
			auth_details = (setting.shopify_url, API_VERSION, setting.get_password("password"))

			previous_flag = getattr(frappe.flags, "shopify_setting", None)
			frappe.flags.shopify_setting = setting.name

			try:
				with Session.temp(*auth_details):
					if accepts_setting_kw:
						kwargs.setdefault("shopify_setting", setting.name)
					return func(*args, **kwargs)
			finally:
				frappe.flags.shopify_setting = previous_flag

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
		shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")
		setting_name = get_shopify_setting_name_by_domain(shop_domain)

		if not setting_name:
			create_shopify_log(
				status="Error",
				request_data=frappe.request.data,
				message=_("Webhook received for unknown Shopify shop domain: {0}").format(shop_domain),
			)
			frappe.throw(_("No Shopify Setting configured for {0}.").format(shop_domain))

		previous_flag = getattr(frappe.flags, "shopify_setting", None)
		frappe.flags.shopify_setting = setting_name
		hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")

		try:
			_validate_request(frappe.request, hmac_header, setting_name)

			data = json.loads(frappe.request.data)
			event = frappe.request.headers.get("X-Shopify-Topic")

			process_request(data, event, setting_name)
		finally:
			frappe.flags.shopify_setting = previous_flag


def process_request(data, event, setting_name):
	if event not in EVENT_MAPPER:
		create_shopify_log(
			status="Error",
			request_data=data,
			message=_("Unhandled Shopify event: {0}").format(event),
			reference_doctype=SETTING_DOCTYPE,
			reference_docname=setting_name,
		)
		return

	# create log
	log = create_shopify_log(
		method=EVENT_MAPPER.get(event, ""),
		request_data=data,
		reference_doctype=SETTING_DOCTYPE,
		reference_docname=setting_name,
	)

	# enqueue backround job
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name, "shopify_setting": setting_name},
	)


def _validate_request(req, hmac_header, setting_name):
	settings = get_shopify_setting_doc(setting_name, require_enabled=True)
	secret_key = settings.shared_secret

	sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

	if sig != bytes(hmac_header.encode()):
		create_shopify_log(
			status="Error",
			request_data=req.data,
			reference_doctype=SETTING_DOCTYPE,
			reference_docname=settings.name,
		)
		frappe.throw(_("Unverified Webhook Data"))
