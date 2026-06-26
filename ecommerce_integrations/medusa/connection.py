# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import base64
import hashlib
import hmac
import json

import frappe
import requests
from frappe import _

from ecommerce_integrations.medusa.constants import EVENT_MAPPER, SETTING_DOCTYPE
from ecommerce_integrations.medusa.utils import create_medusa_log


class MedusaClient:
	"""Thin client over the Medusa **v2 Admin API**.

	Medusa authenticates server-to-server with a *secret admin API key* using HTTP
	Basic auth (the key as the username, an empty password). This collapses Shopify's
	private-app/Session model and Amazon's LWA+SigV4 dance down to a single static
	header — there is no token to refresh.

	Mirrors the role of ``shopify.connection`` + ``unicommerce.api_client``: one
	``request`` method, a handful of named endpoint helpers, and an ``Ecommerce
	Integration Log`` row on failure.
	"""

	def __init__(self, url: str | None = None, api_key: str | None = None):
		setting = frappe.get_doc(SETTING_DOCTYPE)
		base = (url or setting.medusa_url or "").rstrip("/")
		self.base_url = f"{base}/admin"
		key = api_key or setting.get_password("admin_api_key") or ""
		token = base64.b64encode(f"{key}:".encode()).decode()
		self.session = requests.Session()
		self.session.headers.update(
			{
				"Authorization": f"Basic {token}",
				"Content-Type": "application/json",
				"Accept": "application/json",
			}
		)

	# -- low level ----------------------------------------------------------------

	def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
		url = f"{self.base_url}{path}"
		try:
			resp = self.session.request(method, url, params=params, json=body, timeout=30)
			resp.raise_for_status()
			return resp.json() if resp.content else {}
		except Exception as e:
			create_medusa_log(
				status="Error",
				method=f"{method} {path}",
				request_data={"params": params, "body": body},
				message=getattr(getattr(e, "response", None), "text", str(e)),
				exception=e,
				make_new=True,
			)
			raise

	def get(self, path: str, params: dict | None = None) -> dict:
		return self.request("GET", path, params=params)

	def post(self, path: str, body: dict | None = None) -> dict:
		return self.request("POST", path, body=body)

	def delete(self, path: str) -> dict:
		return self.request("DELETE", path)

	def list_all(self, path: str, key: str, params: dict | None = None, page_size: int = 100):
		"""Generator over a paginated Medusa list endpoint.

		Medusa v2 list responses are ``{<key>: [...], count, offset, limit}``.
		"""
		params = dict(params or {})
		offset = 0
		while True:
			params.update({"limit": page_size, "offset": offset})
			data = self.get(path, params=params)
			rows = data.get(key, [])
			yield from rows
			count = data.get("count", 0)
			offset += page_size
			if offset >= count or not rows:
				break

	# -- named endpoints (the surface the sync modules use) ----------------------

	def get_order(self, order_id: str, fields: str | None = None) -> dict:
		params = {"fields": fields} if fields else None
		return self.get(f"/orders/{order_id}", params=params).get("order", {})

	def list_orders(self, params: dict | None = None):
		return self.list_all("/orders", "orders", params=params)

	def get_product(self, product_id: str) -> dict:
		return self.get(f"/products/{product_id}").get("product", {})

	def list_products(self, params: dict | None = None):
		return self.list_all("/products", "products", params=params)

	def create_product(self, body: dict) -> dict:
		return self.post("/products", body=body).get("product", {})

	def update_product(self, product_id: str, body: dict) -> dict:
		return self.post(f"/products/{product_id}", body=body).get("product", {})

	def get_customer(self, customer_id: str) -> dict:
		return self.get(f"/customers/{customer_id}").get("customer", {})

	def get_return(self, return_id: str) -> dict:
		return self.get(f"/returns/{return_id}").get("return", {})

	def list_stock_locations(self):
		return self.list_all("/stock-locations", "stock_locations")

	def update_inventory_level(self, inventory_item_id: str, location_id: str, stocked_quantity: int) -> dict:
		"""Set the stocked quantity for an inventory item at a stock location."""
		return self.post(
			f"/inventory-items/{inventory_item_id}/location-levels/{location_id}",
			body={"stocked_quantity": stocked_quantity},
		)


# -- webhook ingestion (the companion subscriber POSTs here) ----------------------


def get_callback_url() -> str:
	"""URL the Medusa subscriber posts signed events to.

	The merchant pastes this (and the webhook secret) into the shipped
	``medusa_subscriber`` config. Unlike Shopify there is nothing to register via API.
	"""
	url = frappe.request.host if frappe.request else frappe.utils.get_url()
	url = url.replace("https://", "").replace("http://", "")
	return f"https://{url}/api/method/ecommerce_integrations.medusa.connection.store_request_data"


@frappe.whitelist(allow_guest=True)
def store_request_data() -> None:
	if frappe.request:
		hmac_header = frappe.get_request_header("X-Medusa-Hmac-Sha256")
		_validate_request(frappe.request, hmac_header)

		data = json.loads(frappe.request.data)
		event = frappe.get_request_header("X-Medusa-Topic")

		process_request(data, event)


def process_request(data, event):
	# don't run document-mutating handlers when the integration is disabled, even if a
	# stale subscriber still holds the secret and sends correctly-signed events.
	setting = frappe.get_doc(SETTING_DOCTYPE)
	if not setting.is_enabled():
		create_medusa_log(status="Invalid", message="Medusa integration is disabled.", request_data=data)
		return

	if event not in EVENT_MAPPER:
		create_medusa_log(status="Invalid", message=f"Unhandled Medusa topic: {event}", request_data=data)
		return

	# one Ecommerce Integration Log per event; the handler re-attaches via request_id
	log = create_medusa_log(method=EVENT_MAPPER[event], request_data=data)

	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name},
	)


def _validate_request(req, hmac_header):
	setting = frappe.get_doc(SETTING_DOCTYPE)
	secret = setting.get_password("webhook_secret")

	digest = hmac.new(secret.encode("utf8"), req.data, hashlib.sha256).digest()
	computed = base64.b64encode(digest)

	if not hmac_header or not hmac.compare_digest(computed, hmac_header.encode()):
		# req.data is raw bytes; decode so the log can serialise it, and reject with 401
		# rather than letting an unserialisable payload surface as a 500.
		create_medusa_log(
			status="Error", message="Unverified webhook", request_data=frappe.safe_decode(req.data)
		)
		frappe.throw(_("Unverified Webhook Data"), frappe.AuthenticationError)
