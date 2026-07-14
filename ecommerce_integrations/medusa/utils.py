# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

from frappe.utils import get_datetime

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.medusa.constants import MODULE_NAME


def create_medusa_log(**kwargs):
	"""Thin wrapper around the shared ``create_log`` that stamps ``integration=medusa``.

	Mirrors ``ecommerce_integrations.shopify.utils.create_shopify_log``.
	"""
	return create_log(module_def=MODULE_NAME, **kwargs)


def to_amount(value) -> float:
	"""Normalise a Medusa monetary amount to an ERPNext float.

	Medusa v2 stores amounts as decimal major-currency values (BigNumber serialised
	as a number/string), e.g. ``19.99`` — unlike Medusa v1 which used integer minor
	units (cents). We therefore take the value verbatim. If a deployment is found to
	still emit minor units, divide by 100 here in one place.
	"""
	if value is None:
		return 0.0
	return float(value)


def parse_datetime(value):
	"""Parse a Medusa ISO-8601 timestamp into a Frappe datetime (or ``None``)."""
	if not value:
		return None
	# Medusa emits e.g. "2024-06-25T10:11:12.000Z"; frappe.get_datetime handles ISO.
	return get_datetime(str(value).replace("Z", "+00:00"))
