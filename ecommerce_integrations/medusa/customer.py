# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

from typing import Any

import frappe
from frappe.utils import cstr, validate_phone_number

from ecommerce_integrations.controllers.customer import EcommerceCustomer
from ecommerce_integrations.medusa.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	MODULE_NAME,
	SETTING_DOCTYPE,
)


class MedusaCustomer(EcommerceCustomer):
	def __init__(self, customer_id: str):
		self.setting = frappe.get_doc(SETTING_DOCTYPE)
		super().__init__(customer_id, CUSTOMER_ID_FIELD, MODULE_NAME)

	def sync_customer(self, medusa_customer: dict[str, Any]) -> None:
		"""Create Customer (+ addresses + contact) in ERPNext from a Medusa customer dict.

		Mirrors ``shopify.customer.ShopifyCustomer.sync_customer``. The billing and
		shipping addresses come off the *order* (the subscriber attaches them as
		``billing_address``/``shipping_address`` on the customer dict) since Medusa v2
		order addresses are per-order snapshots rather than customer master records.
		"""
		customer_name = cstr(medusa_customer.get("first_name")) + " " + cstr(medusa_customer.get("last_name"))
		if len(customer_name.strip()) == 0:
			customer_name = medusa_customer.get("email")

		customer_group = self.setting.customer_group
		super().sync_customer(customer_name, customer_group)

		email = medusa_customer.get("email")
		billing_address = medusa_customer.get("billing_address") or {}
		shipping_address = medusa_customer.get("shipping_address") or {}

		if billing_address:
			self.create_customer_address(customer_name, billing_address, address_type="Billing", email=email)
		if shipping_address:
			self.create_customer_address(
				customer_name, shipping_address, address_type="Shipping", email=email
			)

		self.create_customer_contact(medusa_customer)

	def create_customer_address(
		self,
		customer_name: str,
		medusa_address: dict[str, Any],
		address_type: str = "Billing",
		email: str | None = None,
	) -> None:
		"""Create a customer Address from a Medusa address dict."""
		address_fields = _map_address_fields(medusa_address, customer_name, address_type, email)
		super().create_customer_address(address_fields)

	def create_customer_contact(self, medusa_customer: dict[str, Any]) -> None:
		if not (medusa_customer.get("first_name") and medusa_customer.get("email")):
			return

		contact_fields = {
			"status": "Passive",
			"first_name": medusa_customer.get("first_name"),
			"last_name": medusa_customer.get("last_name"),
		}

		if medusa_customer.get("email"):
			contact_fields["email_ids"] = [{"email_id": medusa_customer.get("email"), "is_primary": True}]

		phone_no = medusa_customer.get("phone") or (medusa_customer.get("shipping_address") or {}).get(
			"phone"
		)

		if validate_phone_number(phone_no, throw=False):
			contact_fields["phone_nos"] = [{"phone": phone_no, "is_primary_phone": True}]

		super().create_customer_contact(contact_fields)


def _map_address_fields(medusa_address, customer_name, address_type, email):
	"""Map Medusa address fields to the equivalent ERPNext Address fields."""
	address_fields = {
		"address_title": customer_name,
		"address_type": address_type,
		ADDRESS_ID_FIELD: medusa_address.get("id"),  # Verified against @medusajs/types 2.4.0.
		"address_line1": medusa_address.get("address_1") or "Address 1",
		"address_line2": medusa_address.get("address_2"),
		"city": medusa_address.get("city"),
		"state": medusa_address.get("province"),
		"pincode": medusa_address.get("postal_code"),
		"country": _country_from_code(medusa_address.get("country_code")),
		"email_id": email,
	}

	phone = medusa_address.get("phone")
	if validate_phone_number(phone, throw=False):
		address_fields["phone"] = phone

	return address_fields


def _country_from_code(country_code):
	"""Resolve a Medusa ISO 3166-1 alpha-2 country code (e.g. "us") to the ERPNext
	Country name (e.g. "United States"). ERPNext keys Country by name but stores the
	ISO code in the ``code`` field. Falls back to the raw value if unmatched."""
	if not country_code:
		return None
	return frappe.db.get_value("Country", {"code": country_code.lower()}, "name") or country_code
