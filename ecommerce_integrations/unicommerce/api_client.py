from typing import Any, Dict, Optional, Tuple

import frappe
import requests
from frappe import _

from ecommerce_integrations.unicommerce.constants import API_ENDPOINTS, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

JsonDict = Dict[str, Any]


class UnicommerceAPIClient:
	"""Wrapper around Unicommerce REST API

	API docs: https://documentation.unicommerce.com/
	"""

	def __init__(self):
		self.settings = frappe.get_doc(SETTINGS_DOCTYPE)
		self.base_url = f"https://{self.settings.unicommerce_site}"
		self.__initialize_auth()

	def __initialize_auth(self):
		"""Initialize and setup authentication details"""
		self.settings = frappe.get_doc(SETTINGS_DOCTYPE)
		self.settings.renew_tokens()
		self._auth_headers = {"Authorization": f"Bearer {self.settings.get_password('access_token')}"}

	def request(
		self, endpoint: str, method: str = "POST", headers: JsonDict = None, body: JsonDict = None,
	) -> Tuple[JsonDict, bool]:
		if headers is None:
			headers = {}

		headers.update(self._auth_headers)

		if endpoint not in API_ENDPOINTS:
			frappe.throw(_("Undefined Unicommerce API endpoint"))

		url = self.base_url + API_ENDPOINTS[endpoint]
		try:
			response = requests.request(url=url, method=method, headers=headers, json=body)
			response.raise_for_status()
		except Exception:
			create_unicommerce_log(status="Error")

		data = frappe._dict(response.json())
		status = data.successful if data.successful is not None else True

		if not status:
			req = response.request
			url = f"URL: {req.url}"
			body = f"body:  {req.body.decode('utf-8')}"
			request_data = "\n\n".join([url, body])
			message = ", ".join(error["message"] for error in data.errors)
			create_unicommerce_log(
				status="Error", response_data=data, request_data=request_data, message=message
			)

		return data, status

	def get_unicommerce_item(self, sku: str) -> Optional[JsonDict]:
		"""Get Unicommerce item data for specified SKU code.
		Returns None if not found.
		"""
		item, status = self.request(endpoint="get_item", body={"skuCode": sku})
		if status:
			return item
