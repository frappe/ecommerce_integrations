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


	def create_customer_address(self, customer_name, customer_dict: Dict[str, Any]) -> None:
		"""Create customer address(es) using Customer dict provided by shopify."""
		shopify_address = customer_dict.get("default_address", {})

		# map shopify address fields to ERPNext
		address_fields = {
			"address_title": customer_name,
			"address_type": _("Billing"),
			ADDRESS_ID_FIELD: shopify_address.get("id"),
			"address_line1": shopify_address.get("address1") or "Address 1",
			"address_line2": shopify_address.get("address2"),
			"city": shopify_address.get("city"),
			"state": shopify_address.get("province"),
			"pincode": shopify_address.get("zip"),
			"country": shopify_address.get("country"),
			"phone": shopify_address.get("phone"),
			"email_id": customer_dict.get("email"),
		}

		super().create_customer_address(address_fields)

	def create_additional_address(self, customer_name, customer_mail, type, shopify_address: Dict[str, Any]) -> None:
		"""Create customer address(es) using Customer dict provided by shopify."""
		# map shopify address fields to ERPNext
		address_fields = {
			"address_title": customer_name,
			"address_type": _(type),
			"address_line1": shopify_address.get("address1") or "Address 1",
			"address_line2": shopify_address.get("address2"),
			"city": shopify_address.get("city"),
			"state": shopify_address.get("province"),
			"pincode": shopify_address.get("zip"),
			"country": shopify_address.get("country"),
			"phone": shopify_address.get("phone"),
			"email_id": customer_mail,
		}

		super().create_customer_address(address_fields)

	def update_additional_address(self, customer_name, customer_mail, type, shopify_address: Dict[str, Any]) -> None:
		oldAddress = self.get_customer_address_doc(customer_name, _(type))

		if(not oldAddress):
			self.create_additional_address(customer_name, customer_mail, type, shopify_address)
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

	def create_contact(self, first_name, last_name, customer_mail, phone, mobile, marketing) -> None:
		contact_fields = {
			"status": "Passive",
			"first_name": first_name,
			"last_name": last_name,
			"unsubscribed": not marketing
		}

		if(customer_mail):
			contact_fields["email_ids"] =  [{
				"email_id": customer_mail,
				"is_primary": True
			}]

		phone_nos = []
		if(phone):
			phone_nos.append({
				"phone": phone,
				"is_primary_phone": True
			})

		if(mobile):
			phone_nos.append({
				"phone": mobile,
				"is_primary_phone": True
			})

		if(len(phone_nos) > 0):
			contact_fields["phone_nos"] = phone_nos

		super().create_customer_contact(contact_fields)
