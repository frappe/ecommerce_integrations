import frappe

from ecommerce_integrations.medusa.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	RETURN_ID_FIELD,
)


def before_uninstall():
	# This large table is linked with "modules" hence gets deleted one by one
	frappe.db.delete("Ecommerce Integration Log")

	_delete_medusa_custom_fields()


def _delete_medusa_custom_fields():
	"""Remove the custom fields the Medusa connector installs on standard doctypes."""
	fieldnames = (
		CUSTOMER_ID_FIELD,
		ADDRESS_ID_FIELD,
		ORDER_ID_FIELD,
		ORDER_NUMBER_FIELD,
		ORDER_STATUS_FIELD,
		FULFILLMENT_ID_FIELD,
		RETURN_ID_FIELD,
		ORDER_ITEM_DISCOUNT_FIELD,
		ITEM_SELLING_RATE_FIELD,
	)
	frappe.db.delete("Custom Field", {"fieldname": ("in", fieldnames)})
