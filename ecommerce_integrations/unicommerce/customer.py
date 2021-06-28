from typing import Any, Dict, List

import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of

from ecommerce_integrations.unicommerce.constants import (
	SETTINGS_DOCTYPE,
	UNICOMMERCE_COUNTRY_MAPPING,
)


def sync_customer(order):
	"""Using order create a new customer.

	There is no unique identified for customer, so a new customer is created for every order"""
	customer = _create_new_customer(order)
	_create_customer_addresses(order.get("addresses") or [], customer)
	return customer


def _create_new_customer(order) -> None:
	"""Create a new customer from Sales Order address data"""

	setting = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	customer_group = (
		frappe.db.get_value(
			"Unicommerce Channel", {"channel_id": order["channel"]}, fieldname="customer_group"
		)
		or setting.default_customer_group
	)

	address = order.get("billingAddress") or (order.get("addresses") and order.get("addresses")[0])
	name = address.get("name") or order["channel"] + " customer"

	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_group": customer_group,
			"territory": get_root_of("Territory"),
			"customer_type": "Individual",
		}
	)

	customer.flags.ignore_mandatory = True
	customer.insert(ignore_permissions=True)

	return customer


def _create_customer_addresses(addresses: List[Dict[str, Any]], customer) -> None:
	"""Create address from dictionary containing fields used in Address doctype of ERPNext.

	Unicommerce orders contain address list,
	if there is only one address it's both shipping and billing,
	else first is billing and second is shipping"""

	if len(addresses) == 1:
		_create_customer_address(addresses[0], "Billing", customer)
	elif len(addresses) >= 2:
		_create_customer_address(addresses[0], "Billing", customer)
		_create_customer_address(addresses[1], "Shipping", customer)


def _create_customer_address(uni_address, address_type, customer):
	frappe.get_doc(
		{
			"address_line1": uni_address.get("addressLine1") or "Not provided",
			"address_line2": uni_address.get("addressLine2"),
			"address_type": address_type,
			"city": uni_address.get("city"),
			"country": UNICOMMERCE_COUNTRY_MAPPING.get(uni_address.get("country")),
			"county": uni_address.get("district"),
			"doctype": "Address",
			"email_id": uni_address.get("email"),
			"phone": uni_address.get("phone"),
			"pincode": uni_address.get("pincode"),
			"state": uni_address.get("state"),
			"links": [{"link_doctype": "Customer", "link_name": customer.name}],
		}
	).insert(ignore_mandatory=True)
