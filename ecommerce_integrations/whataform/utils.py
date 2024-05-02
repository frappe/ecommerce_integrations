# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.whataform.constants import MODULE_NAME


def create_whataform_log(**kwargs):
	return create_log(module_def=MODULE_NAME, **kwargs)


class KeyValueErrorClass:
	def __init__(self, **kwargs):
		self.kwargs = kwargs

	def __str__(self):
		key_value_pairs = ", ".join(f"{key}={value}" for key, value in self.kwargs.items())
		return f"{self.message} ({key_value_pairs})"


class UnderspecifiedCustomer(KeyValueErrorClass, LookupError):
	def __init__(self, **kwargs):
		self.message = "Underspecified Customer"
		super().__init__(**kwargs)
		self.add_note("You may need to set both email and whatasapp fields mandatory in your whataform")


class UnderspecifiedNewCustomer(KeyValueErrorClass, frappe.exceptions.ValidationError):
	def __init__(self, **kwargs):
		self.message = "Underspecified NEW Customer"
		super().__init__(**kwargs)
		self.add_note("You may need to configure the mandatory fields as per your Whataform Setting")


class UnderspecifiedNewContact(KeyValueErrorClass, frappe.exceptions.ValidationError):
	def __init__(self, **kwargs):
		self.message = "Underspecified NEW Contact"
		super().__init__(**kwargs)
		self.add_note("You may need to configure the mandatory fields as per your Whataform Setting")


class UnderspecifiedAddress(KeyValueErrorClass, frappe.exceptions.ValidationError):
	def __init__(self, **kwargs):
		self.message = "Underspecified Address"
		super().__init__(**kwargs)
		self.add_note("You may need to add mandatory fields to accurately capture the address")


class NoSkuInWebhookData(KeyValueErrorClass, frappe.exceptions.ValidationError):
	def __init__(self, data=None):
		self.message = "No SKU in data"
		super().__init__(product=data.get("product"))
		self.add_note(
			"You may need to configure an sku in whataform for item {}".format(json.dumps(data, indent=2))
		)


class NoItemMapping(KeyValueErrorClass, frappe.exceptions.ValidationError):
	def __init__(self, sku=None):
		self.message = "No item mapping"
		super().__init__(sku=sku)
		self.add_note("You may need to configure an sku to item mapping in ERPNext")
