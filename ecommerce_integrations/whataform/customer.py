import json
import re
from typing import Any, Dict, Optional

import frappe
from frappe import _
from frappe.utils import cstr, validate_phone_number
from unidecode import unidecode

from ecommerce_integrations.controllers.customer import EcommerceCustomer
from ecommerce_integrations.whataform.constants import (
	CUSTOMER_ID_FIELD,
	MODULE_NAME,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.whataform.utils import (
	UnderspecifiedAddress,
	UnderspecifiedCustomer,
	UnderspecifiedNewContact,
	UnderspecifiedNewCustomer,
)

phone_cleanup = {
	ord(" "): None,
	ord("-"): None,
	ord("("): None,
	ord(")"): None,
	ord("+"): None,
}


def sanitize_address_lines(address):
	address_line1 = (address.get("address_line1")).upper()
	# add spaces before numbers
	address_line1 = re.sub(r"(\D)(\d)", "\\1 \\2", address_line1)
	# add spaces after numbers
	address_line1 = re.sub(r"(\d)(\D)", "\\1 \\2", address_line1)
	# add spaces before #
	address_line1 = re.sub(r"([^#])(#)", "\\1 \\2", address_line1)
	# add spaces after #
	address_line1 = re.sub(r"(#)([^#])", "\\1 \\2", address_line1)
	# add spaces before -
	address_line1 = re.sub(r"([^\-])(-)", "\\1 \\2", address_line1)
	# add spaces after -
	address_line1 = re.sub(r"(-)([^\-])", "\\1 \\2", address_line1)
	# remove excess spaces
	address_line1 = re.sub(r" +", " ", address_line1)

	address_line2 = address.get("address_line2").title() or None
	# add spaces after colons
	address_line2 = re.sub(r"(,)(\w)", "\\1 \\2", address_line2)
	# add spaces before #
	address_line2 = re.sub(r"([^#])(#)", "\\1 \\2", address_line2)
	# add spaces after #
	address_line2 = re.sub(r"(#)([^#])", "\\1 \\2", address_line2)
	# add spaces before -
	address_line2 = re.sub(r"([^\-])(-)", "\\1 \\2", address_line2)
	# add spaces after -
	address_line2 = re.sub(r"(-)([^\-])", "\\1 \\2", address_line2)
	# remove excess spaces
	address_line2 = re.sub(r" +", " ", address_line2)

	address["address_line1"] = address_line1
	address["address_line2"] = address_line2
	address["city"] = unidecode(cstr(address.get("city")).title()) or None


class WhataformCustomer(EcommerceCustomer):
	def __init__(self, email_id: str, mobile_no: str):
		self.setting = frappe.get_doc(SETTING_DOCTYPE)
		if not email_id or not mobile_no:
			err = UnderspecifiedCustomer(email_id=email_id, mobile_no=mobile_no)
			raise err
		self.email_id = email_id
		self.mobile_no = mobile_no.translate(phone_cleanup)
		customer_id = hash(self.email_id + self.mobile_no)
		super().__init__(customer_id, CUSTOMER_ID_FIELD, MODULE_NAME)

	def is_matched(self) -> bool:
		if self.is_synced():
			return True
		try:
			if customer := frappe.get_last_doc(
				"Customer", {"email_id": self.email_id, "mobile_no": self.mobile_no}
			):
				self.link(customer)
				return True
		except frappe.exceptions.DoesNotExistError:
			pass

	def create_customer(self, payload: Dict[str, Any]) -> None:
		"""Create Customer in ERPNext using whataform's responses dict."""

		first_name = payload.get(self.setting.first_name_field_key)
		last_name = payload.get(self.setting.last_name_field_key)

		address = {}
		address["address_line1"] = payload.get(self.setting.address_line1_field_key)
		address["address_line2"] = payload.get(self.setting.address_line2_field_key)
		address["city"] = payload.get(self.setting.city_field_key)
		address["country"] = payload.get(self.setting.country_field_key)
		if not address.get("country"):
			address["country"] = self.setting.default_country

		if not (first_name and last_name and self.email_id and self.mobile_no):
			err = UnderspecifiedNewCustomer(
				email_id=self.email_id, mobile_no=self.mobile_no, first_name=first_name, last_name=last_name
			)
			raise err

		customer_name = cstr(first_name).title() + " " + cstr(last_name).title()
		customer_group = self.setting.customer_group

		super().sync_customer(customer_name, customer_group)

		self.create_customer_address(
			payload, customer_name, address_type="Billing", primary=True,
		)
		self.create_customer_contact(payload)
		# In case of sync failure, keep at least the entire customer
		frappe.db.commit()  # nosemgrep

	def create_customer_address(
		self,
		payload: Dict[str, Any],
		customer_name,
		address_type: str = "Billing",
		primary: bool = False,
	) -> None:
		"""Create customer address(es) using custom fields provided by whataform."""
		address_fields = self._map_address_fields(payload, customer_name, address_type)
		super().create_customer_address(address_fields, primary=primary)

	def update_existing_addresses(self, payload):
		customer = self.get_customer_doc()
		old_address = self.get_customer_address_doc("Billing")
		if not old_address:
			self.create_customer_address(payload, customer.customer_name)
		else:
			exclude_in_update = ["address_title", "address_type"]
			new_values = self._map_address_fields(payload, customer.customer_name, "Billing")

			old_address.update({k: v for k, v in new_values.items() if k not in exclude_in_update})
			old_address.flags.ignore_mandatory = True
			old_address.save()
			# In case of sync failure, keep at least the entire customer
			frappe.db.commit()  # nosemgrep

	def create_customer_contact(self, payload: Dict[str, Any]) -> None:

		first_name = payload.get(self.setting.first_name_field_key)
		last_name = payload.get(self.setting.last_name_field_key)

		if not (first_name and last_name and self.email_id and self.mobile_no):
			err = UnderspecifiedNewContact(
				email_id=self.email_id, mobile_no=self.mobile_no, first_name=first_name, last_name=last_name
			)
			raise err

		contact_fields = {
			"status": "Passive",
			"first_name": first_name or None,
			"last_name": last_name or None,
			# "unsubscribed": not payload.get("accepts_marketing"),
		}

		contact_fields["email_ids"] = [{"email_id": self.email_id, "is_primary": True}]

		contact_fields["phone_nos"] = [
			{"phone": self.mobile_no, "is_primary_phone": True, "is_primary_mobile_no": True}
		]

		super().create_customer_contact(contact_fields)

	def _map_address_fields(self, payload, customer_name, address_type):
		""" returns dict with payload fields mapped to equivalent ERPNext fields"""
		from frappe.geo import utils

		address = {}
		address["address_line1"] = payload.get(self.setting.address_line1_field_key)
		address["address_line2"] = payload.get(self.setting.address_line2_field_key)
		address["pincode"] = payload.get(self.setting.zip_code_field_key)
		address["city"] = payload.get(self.setting.city_field_key)
		address["country"] = payload.get(self.setting.country_field_key)
		if not address.get("country"):
			address["country"] = self.setting.default_country
		if payload.get(self.setting.google_address_field_key):
			google_address_detail = payload.get(self.setting.google_address_field_key + "_detail")
			line = google_address_detail.get("address_line_1")
			components = line.split(",")
			address["address_line1"] = components[0].strip()
			address["city"] = components[1].strip()
			address["address_line2"] = google_address_detail.get("address_line_2")
			lat = google_address_detail.get("lat")
			lng = google_address_detail.get("lng")
			address["longitude"] = lng
			address["latitude"] = lat
			data = frappe._dict({"name": customer_name, "latitude": lat, "longitude": lng,})
			address["location"] = json.dumps(utils.convert_to_geojson("coordinates", [data]))
			address["location_reviewed"] = True

		if not (address.get("address_line1") and address.get("address_line2") and address.get("city")):
			first_name = payload.get(self.setting.first_name_field_key)
			last_name = payload.get(self.setting.last_name_field_key)
			err = UnderspecifiedAddress(
				email_id=self.email_id,
				mobile_no=self.mobile_no,
				first_name=first_name,
				last_name=last_name,
				address=address,
			)
			raise err

		sanitize_address_lines(address)

		address_fields = dict(
			{
				"address_title": customer_name,
				"address_type": address_type,
				"email_id": self.email_id,
				"phone": self.mobile_no,
			},
			**address,
		)

		return address_fields
