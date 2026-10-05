# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import json
from typing import Optional

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint
from frappe.utils.file_manager import save_file

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	MANIFEST_GENERATED_CHECK,
	ORDER_CODE_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
)
from ecommerce_integrations.unicommerce.invoice import fetch_pdf_as_base64
from ecommerce_integrations.unicommerce.utils import remove_non_alphanumeric_chars

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

	def before_submit(self):
		self.create_and_close_manifest_on_unicommerce()
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

			if cint(package_info.get(MANIFEST_GENERATED_CHECK)):
				frappe.throw(
					_("Row #{}: Manifest is already generated, please remove package.").format(package.idx)
				)

			for invoice_field, manifest_field in FIELD_MAPPING.items():
				package.set(manifest_field, package_info[invoice_field])
				package.awb_barcode = package.awb_no

	def get_facility_code(self) -> str:
		facility_codes = {package.facility_code for package in self.manifest_items}
		if len(facility_codes) != 1:
			frappe.throw(
				_("Shipping manifest should only have one facility code, found: {}").format(
					",".join(facility_codes)
				)
			)
		return next(iter(facility_codes))

	def create_and_close_manifest_on_unicommerce(self):
		shipping_packages = [d.shipping_package_code for d in self.manifest_items]

		facility_code = self.get_facility_code()

		client = UnicommerceAPIClient()

		response = client.create_and_close_shipping_manifest(
			channel=self.channel_id,
			shipping_provider_code=self.shipping_provider_code,
			shipping_method_code=self.shipping_method_code,
			shipping_packages=shipping_packages,
			facility_code=facility_code,
			third_party_shipping=self.third_party_shipping,
		)

		if not response:
			frappe.throw(_("Failed to Generate Manifest on Unicommerce"))

		status = response.get("shippingManifestStatus")

		pdf_link = status.get("shippingManifestLink")
		manifest_code = status.get("shippingManifestCode")
		manifest_id = status.get("id")
		self.unicommerce_manifest_code = manifest_code
		self.unicommerce_manifest_id = manifest_id

		self.attach_unicommerce_manifest_pdf(pdf_link, manifest_code)

	def attach_unicommerce_manifest_pdf(self, link, manifest_code):
		if not link:
			return

		pdf_b64 = fetch_pdf_as_base64(link)
		if not pdf_b64:
			return

		manifest_code = remove_non_alphanumeric_chars(manifest_code)

		save_file(
			f"unicommerce-manifest-{manifest_code}.pdf",
			pdf_b64,
			self.doctype,
			self.name,
			decode=True,
			is_private=1,
		)

	def update_manifest_status(self):
		si_codes = [package.sales_invoice for package in self.manifest_items]
		frappe.db.set_value("Sales Invoice", {"name": ("in", si_codes)}, MANIFEST_GENERATED_CHECK, 1)


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
			MANIFEST_GENERATED_CHECK,
		],
		as_dict=True,
	)

	items = frappe.db.get_values("Sales Invoice Item", {"parent": sales_invoice}, "item_name", as_dict=True)

	unique_items = {item.item_name for item in items}
	si_data["item_list"] = ",".join(unique_items)

	return si_data


@frappe.whitelist()
def search_packages(search_term: str, channel: str | None = None, shipper: str | None = None):
	filters = {
		CHANNEL_ID_FIELD: channel,
		SHIPPING_PROVIDER_CODE: shipper,
		MANIFEST_GENERATED_CHECK: 0,
	}

	# remove non-existing values
	filters = {k: v for k, v in filters.items() if v is not None}

	or_filters = {
		TRACKING_CODE_FIELD: search_term,
		SHIPPING_PACKAGE_CODE_FIELD: search_term,
		INVOICE_CODE_FIELD: search_term,
	}

	packages = frappe.get_list("Sales Invoice", filters=filters, or_filters=or_filters, limit_page_length=1)

	if packages:
		return packages[0].name


@frappe.whitelist()
def get_shipping_package_list(source_name, target_doc=None):
	if target_doc and isinstance(target_doc, str):
		target_doc = json.loads(target_doc)

	target_doc.setdefault("manifest_items", []).append({"sales_invoice": source_name})

	return target_doc
