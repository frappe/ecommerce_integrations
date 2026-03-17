import base64
import functools
import hashlib
import hmac
import json

import frappe
from frappe import _
from shopify import GraphQL, Session

from ecommerce_integrations.shopify.constants import (
	API_VERSION,
	EVENT_MAPPER,
	SETTING_DOCTYPE,
	WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def temp_shopify_session(func):
	"""Any function that needs to access shopify api needs this decorator. The decorator starts a temp session that's destroyed when function returns."""

	@functools.wraps(func)
	def wrapper(*args, **kwargs):
		# no auth in testing
		if frappe.flags.in_test:
			return func(*args, **kwargs)

		setting = frappe.get_doc(SETTING_DOCTYPE)
		if setting.is_enabled():
			auth_details = (
				setting.shopify_url,
				API_VERSION,
				setting.get_password("password"),
			)

			with Session.temp(*auth_details):
				return func(*args, **kwargs)

	return wrapper


def register_webhooks(shopify_url: str, password: str) -> list[dict]:
	"""Register required webhooks using Shopify GraphQL API."""

	new_webhooks = []

	# Remove old webhooks
	unregister_webhooks(shopify_url, password)

	mutation = """
    mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $callbackUrl: URL!) {
      webhookSubscriptionCreate(
        topic: $topic
        webhookSubscription: { format: JSON, callbackUrl: $callbackUrl }
      ) {
        webhookSubscription {
          id
          topic
          endpoint {
            __typename
            ... on WebhookHttpEndpoint {
              callbackUrl
            }
          }
        }
        userErrors {
          field
          message
        }
      }
    }
    """

	with Session.temp(shopify_url, API_VERSION, password):
		for topic in WEBHOOK_EVENTS:
			# Ensure JSON object result
			raw = GraphQL().execute(
				mutation,
				{
					"topic": topic,
					"callbackUrl": get_callback_url(),
				},
			)

			try:
				result = json.loads(raw) if isinstance(raw, str) else raw
			except Exception:
				create_shopify_log(status="Error", message="Invalid GraphQL response", response_data=raw)
				continue

			# Core nodes
			root = result.get("data")
			errors = result.get("errors")

			# If `data` is None => fatal GraphQL error
			if root is None:
				msg = errors[0].get("message") if errors else "Unknown Shopify GraphQL error"
				create_shopify_log(
					status="Error",
					message=msg,
					response_data=result,
					exception=errors,
				)
				continue

			create_node = root.get("webhookSubscriptionCreate")

			if not create_node:
				create_shopify_log(
					status="Error",
					message="Missing webhookSubscriptionCreate",
					response_data=result,
				)
				continue

			# User errors
			user_errors = create_node.get("userErrors") or []
			if user_errors:
				msg = user_errors[0].get("message")
				create_shopify_log(
					status="Error",
					message=msg,
					response_data=result,
					exception=user_errors,
				)
				continue

			webhook = create_node.get("webhookSubscription")
			if webhook:
				new_webhooks.append(webhook)
			query = """
                    query {
                webhookSubscriptionsCount {
                    count
                    precision
                }
                }
            """
			response = GraphQL().execute(query)
			create_shopify_log(
				status="Success", message="Webhooks added to current url", response_data=response
			)
	return new_webhooks


def unregister_webhooks(shopify_url: str, password: str) -> None:
	"""Unregister all GraphQL webhooks for the current site URL."""

	query = """
    {
      webhookSubscriptions(first: 250) {
        edges {
          node {
            id
            endpoint {
              __typename
              ... on WebhookHttpEndpoint {
                callbackUrl
              }
            }
          }
        }
      }
    }
    """

	delete_mutation = """
    mutation webhookSubscriptionDelete($id: ID!) {
      webhookSubscriptionDelete(id: $id) {
        deletedWebhookSubscriptionId
        userErrors {
          field
          message
        }
      }
    }
    """

	with Session.temp(shopify_url, API_VERSION, password):
		result_raw = GraphQL().execute(query)

	try:
		result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
	except Exception:
		frappe.log_error(f"Invalid GraphQL response: {result_raw}", "Shopify Unregister Webhooks")
		return

	edges = result.get("data", {}).get("webhookSubscriptions", {}).get("edges", [])

	for edge in edges:
		node = edge.get("node", {})
		webhook_id = node.get("id")
		if webhook_id:
			with Session.temp(shopify_url, API_VERSION, password):
				response = GraphQL().execute(delete_mutation, {"id": webhook_id})
				create_shopify_log(
					status="Success", message="Webhook deleted for the current url", response_data=response
				)


def get_current_domain_name() -> str:
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

		_validate_request(frappe.request, hmac_header)

		data = json.loads(frappe.request.data)

		event = frappe.request.headers.get("X-Shopify-Topic")

		process_request(data, event)


def process_request(data, event):
	log = create_shopify_log(method=EVENT_MAPPER[event], request_data=data)

	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name},
	)


def _validate_request(req, hmac_header):
	settings = frappe.get_doc(SETTING_DOCTYPE)
	secret_key = settings.shared_secret
	raw_body = req.get_data()

	computed_hmac = base64.b64encode(
		hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha256).digest()
	).decode()

	if not hmac.compare_digest(computed_hmac, hmac_header):
		frappe.throw(_("Unverified Webhook Data"))
