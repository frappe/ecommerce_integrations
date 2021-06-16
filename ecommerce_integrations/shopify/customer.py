from typing import Any, Dict, Optional

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

		billing_address = customer.get("billing_address", {}) or customer.get("default_address")
		shipping_address = customer.get("shipping_address", {})

		if billing_address:
			self.create_customer_address(
				customer_name, billing_address, address_type="Billing", email=customer.get("email")
			)
		if shipping_address:
			self.create_customer_address(
				customer_name, shipping_address, address_type="Shipping", email=customer.get("email")
			)

		self.create_customer_contact(customer)

	def create_customer_address(
		self,
		customer_name,
		shopify_address: Dict[str, Any],
		address_type: str = "Billing",
		email: Optional[str] = None,
	) -> None:
		"""Create customer address(es) using Customer dict provided by shopify."""
		address_fields = {
			"address_title": customer_name,
			"address_type": address_type,
			ADDRESS_ID_FIELD: shopify_address.get("id"),
			"address_line1": shopify_address.get("address1") or "Address 1",
			"address_line2": shopify_address.get("address2"),
			"city": shopify_address.get("city"),
			"state": shopify_address.get("province"),
			"pincode": shopify_address.get("zip"),
			"country": shopify_address.get("country"),
			"phone": shopify_address.get("phone"),
			"email_id": email,
		}

		super().create_customer_address(address_fields)

	def update_additional_address(
		self, customer_name, customer_mail, type, shopify_address: Dict[str, Any]
	) -> None:
		oldAddress = self.get_customer_address_doc(customer_name, _(type))

		if not oldAddress:
			self.create_customer_address(customer_name, shopify_address, type, customer_mail)
		else:
			oldAddress.address_line1 = shopify_address.get("address1") or "Address 1"
			oldAddress.address_line2 = shopify_address.get("address2")
			oldAddress.city = shopify_address.get("city")
			oldAddress.state = shopify_address.get("province")
			oldAddress.pincode = shopify_address.get("zip")
			oldAddress.country = shopify_address.get("country")
			oldAddress.phone = shopify_address.get("phone")
			oldAddress.email_id = customer_mail
			oldAddress.save()

	def create_customer_contact(self, shopify_customer: Dict[str, Any]) -> None:

		if not (shopify_customer.get("first_name") and shopify_customer.get("email")):
			return

		contact_fields = {
			"status": "Passive",
			"first_name": shopify_customer.get("first_name"),
			"last_name": shopify_customer.get("last_name"),
			"unsubscribed": not shopify_customer.get("accepts_marketing"),
		}

		if shopify_customer.get("email"):
			contact_fields["email_ids"] = [{"email_id": shopify_customer.get("email"), "is_primary": True}]


		phone_no = shopify_customer.get("phone") or shopify_customer.get("default_address", {}).get("phone")

		if phone_no:
			contact_fields["phone_nos"] = [{"phone": phone_no, "is_primary_phone": True}]

		super().create_customer_contact(contact_fields)
