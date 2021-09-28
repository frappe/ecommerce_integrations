# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	MANIFEST_STATUS_FIELD,
	ORDER_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
)

# mapping of invoice field to manifest package fields
FIELD_MAPPING = {
	TRACKING_CODE_FIELD: "awb_no",
	FACILITY_CODE_FIELD: "facility_code",
	ORDER_CODE_FIELD: "unicommerce_sales_order",
	SHIPPING_PACKAGE_CODE_FIELD: "shipping_package_code",
	"shipping_address": "shipping_address",
	"item_list": "item_list",
}


class UnicommerceShipmentManifest(Document):
	def validate(self):
		self.set_shipping_method()
		self.set_unicommerce_details()

	def on_submit(self):
		self.update_manifest_status()

	def set_shipping_method(self):
		self.third_party_shipping = cint(
			frappe.db.get_value("Unicommerce Channel", self.channel_id, "shipping_handled_by_marketplace")
		)

	def set_unicommerce_details(self):
		"""In packages table fetch all relevant info and validate invoices."""

		for package in self.manifest_items:
			package_info = get_sales_invoice_details(package.sales_invoice)

			if self.channel_id != package_info.get(CHANNEL_ID_FIELD):
				frappe.throw(
					_("Row #{} : Only {} channel packages can be added in this manifest").format(
						package.idx, self.channel_id
					)
				)

			if cint(package_info.get(MANIFEST_STATUS_FIELD)):
				frappe.throw(
					_("Row #{}: Manifest is already generated, please remove package.").format(package.idx)
				)

			for invoice_field, manifest_field in FIELD_MAPPING.items():
				package.set(manifest_field, package_info[invoice_field])

	def get_facility_code(self) -> str:
		facility_codes = {package.facility_code for package in self.manifest_items}
		if len(facility_codes) != 1:
			frappe.throw(
				_("Shipping manifest should only have one facility code, found: {}").format(
					",".join(facility_codes)
				)
			)
		return list(facility_codes)[0]

	def update_manifest_status(self):
		si_codes = [package.sales_invoice for package in self.manifest_items]
		frappe.db.set_value("Sales Invoice", {"name": ("in", si_codes)}, MANIFEST_STATUS_FIELD, 1)


def get_sales_invoice_details(sales_invoice):
	si_data = frappe.db.get_value(
		"Sales Invoice",
		sales_invoice,
		[
			"shipping_address",
			CHANNEL_ID_FIELD,
			FACILITY_CODE_FIELD,
			ORDER_CODE_FIELD,
			SHIPPING_PACKAGE_CODE_FIELD,
			SHIPPING_PROVIDER_CODE,
			TRACKING_CODE_FIELD,
			MANIFEST_STATUS_FIELD,
		],
		as_dict=True,
	)

	items = frappe.db.get_values(
		"Sales Invoice Item", {"parent": sales_invoice}, "item_name", as_dict=True
	)

	unique_items = {item.item_name for item in items}
	si_data["item_list"] = ",".join(unique_items)

	return si_data
