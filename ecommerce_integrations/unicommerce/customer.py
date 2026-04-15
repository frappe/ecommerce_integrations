import json
from typing import Any

import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of

from ecommerce_integrations.unicommerce.constants import (
	ADDRESS_JSON_FIELD,
	CUSTOMER_CODE_FIELD,
	SETTINGS_DOCTYPE,
	UNICOMMERCE_COUNTRY_MAPPING,
	UNICOMMERCE_INDIAN_STATES_MAPPING,
)


def sync_customer(order):
	"""Using order create a new customer.

	Note: Unicommerce doesn't deduplicate customer."""
	customer = _create_new_customer(order)
	_create_customer_addresses(order.get("addresses") or [], customer)
	return customer


def _create_new_customer(order):
	"""Create a new customer from Sales Order address data"""

	address = order.get("billingAddress") or (order.get("addresses") and order.get("addresses")[0])
	address.pop("id", None)  # this is not important and can be different for same address
	customer_code = order.get("customerCode")

	customer = _check_if_customer_exists(address, customer_code)
	if customer:
		return customer

	setting = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	customer_group = (
		frappe.db.get_value(
			"Unicommerce Channel", {"channel_id": order["channel"]}, fieldname="customer_group"
		)
		or setting.default_customer_group
	)

	name = address.get("name") or order["channel"] + " customer"
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_group": customer_group,
			"territory": get_root_of("Territory"),
			"customer_type": "Individual",
			ADDRESS_JSON_FIELD: json.dumps(address),
			CUSTOMER_CODE_FIELD: customer_code,
		}
	)

	customer.flags.ignore_mandatory = True
	customer.insert(ignore_permissions=True)

	return customer


def _check_if_customer_exists(address, customer_code):
	"""Very crude method to determine if same customer exists.

	If ALL address fields match then new customer is not created"""

	customer_name = None

	if customer_code:
		customer_name = frappe.db.get_value("Customer", {CUSTOMER_CODE_FIELD: customer_code})

	if not customer_name:
		customer_name = frappe.db.get_value("Customer", {ADDRESS_JSON_FIELD: json.dumps(address)})

	if customer_name:
		return frappe.get_doc("Customer", customer_name)


def _create_customer_addresses(addresses: list[dict[str, Any]], customer) -> None:
	"""Create address from dictionary containing fields used in Address doctype of ERPNext.

	Unicommerce orders contain address list,
	if there is only one address it's both shipping and billing,
	else first is billing and second is shipping"""

	if len(addresses) == 1:
		_create_customer_address(addresses[0], "Billing", customer, also_shipping=True)
	elif len(addresses) >= 2:
		_create_customer_address(addresses[0], "Billing", customer)
		_create_customer_address(addresses[1], "Shipping", customer)


def _create_customer_address(uni_address, address_type, customer, also_shipping=False):
	country_code = uni_address.get("country")
	country = UNICOMMERCE_COUNTRY_MAPPING.get(country_code)

	state = uni_address.get("state")
	if country_code == "IN" and state in UNICOMMERCE_INDIAN_STATES_MAPPING:
		state = UNICOMMERCE_INDIAN_STATES_MAPPING.get(state)

	frappe.get_doc(
		{
			"address_line1": uni_address.get("addressLine1") or "Not provided",
			"address_line2": uni_address.get("addressLine2"),
			"address_type": address_type,
			"city": uni_address.get("city"),
			"country": country,
			"county": uni_address.get("district"),
			"doctype": "Address",
			"email_id": uni_address.get("email"),
			"phone": uni_address.get("phone"),
			"pincode": uni_address.get("pincode"),
			"state": state,
			"links": [{"link_doctype": "Customer", "link_name": customer.name}],
			"is_primary_address": int(address_type == "Billing"),
			"is_shipping_address": int(also_shipping or address_type == "Shipping"),
		}
	).insert(ignore_mandatory=True)
