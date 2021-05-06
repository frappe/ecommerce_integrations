from typing import Any, Dict

import frappe
from frappe import _

from ecommerce_integrations.controllers.customer import EcommerceCustomer
from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	MODULE_NAME,
	SETTING_DOCTYPE,
)


class ShopifyCustomer(EcommerceCustomer):
	def __init__(self, customer_id: str):
		self.setting = frappe.get_doc(SETTING_DOCTYPE)
		super().__init__(customer_id, CUSTOMER_ID_FIELD, MODULE_NAME)

	def sync_customer(self, customer: Dict[str, Any]) -> None:
		"""Create Customer in ERPNext using shopify's Customer dict."""

		customer_name = customer.get("first_name", "") + " " + customer.get("last_name", "")
		if len(customer_name.strip()) == 0:
			customer_name = customer.get("email")

		customer_group = self.setting.customer_group
		super().sync_customer(customer_name, customer_group)

		self.create_customer_address(customer_name, customer)

	def create_customer_address(
		self, customer_name, customer_dict: Dict[str, Any]
	) -> None:
		"""Create customer address(es) using Customer dict provided by shopify."""
		try:
			shppify_address = customer_dict.get("default_address", {})

			# map shopify address fields to ERPNext
			address_fields = {
				"address_title": customer_name,
				"address_type": _("Billing"),
				ADDRESS_ID_FIELD: shppify_address.get("id"),
				"address_line1": shppify_address.get("address1") or "Address 1",
				"address_line2": shppify_address.get("address2"),
				"city": shppify_address.get("city"),
				"state": shppify_address.get("province"),
				"pincode": shppify_address.get("zip"),
				"country": shppify_address.get("country"),
				"phone": shppify_address.get("phone"),
				"email_id": customer_dict.get("email"),
			}

			super().create_customer_address(address_fields)

		except Exception as e:
			raise e
