import base64
import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt

from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	FACILITY_CODE_FIELD,
	INVOICE_CODE_FIELD,
	IS_COD_CHECKBOX,
	SETTINGS_DOCTYPE,
	SHIPPING_METHOD_FIELD,
	SHIPPING_PACKAGE_CODE_FIELD,
	SHIPPING_PACKAGE_STATUS_FIELD,
	SHIPPING_PROVIDER_CODE,
	TRACKING_CODE_FIELD,
)


logger = frappe.logger("unicommerce_invoice", allow_site=True, file_count=20)


def _log_info(message: str, request_data: dict | None = None):
	logger.info(message)
	create_unicommerce_log(status="Info", message=message, request_data=request_data)


def _log_success(message: str, request_data: dict | None = None):
	logger.info(message)
	create_unicommerce_log(status="Success", message=message, request_data=request_data)


def _log_failure(message: str, request_data: dict | None = None, exception: Exception | None = None):
	logger.error(message)
	if exception:
		frappe.log_error(frappe.get_traceback(), message)
	create_unicommerce_log(
		status="Error" if exception else "Failure",
		message=message,
		request_data=request_data,
		exception=exception,
		rollback=bool(exception),
	)


def _get_company_state_code(company: str) -> str | None:
	company_gstin = frappe.db.get_value("Company", company, "gstin")
	if company_gstin:
		return str(company_gstin)[:2]
	return None


def _get_party_state_code(si) -> str | None:
	customer_gstin = frappe.db.get_value("Customer", si.customer, "gstin")
	if customer_gstin:
		return str(customer_gstin)[:2]

	shipping_address = si.shipping_address_name or si.customer_address
	if shipping_address:
		gst_state_number = frappe.db.get_value("Address", shipping_address, "gst_state_number")
		if gst_state_number:
			return str(gst_state_number)

	return None


def _get_gst_tax_template(si) -> str | None:
	company = si.company
	company_state_code = _get_company_state_code(company)
	party_state_code = _get_party_state_code(si)

	is_inter_state = bool(
		company_state_code and party_state_code and str(company_state_code) != str(party_state_code)
	)

	if is_inter_state:
		template = frappe.db.get_value(
			"Sales Taxes and Charges Template",
			{
				"company": company,
				"disabled": 0,
				"is_inter_state": 1,
			},
			"name",
		)
		if template:
			return template
	else:
		template = frappe.db.get_value(
			"Sales Taxes and Charges Template",
			{
				"company": company,
				"disabled": 0,
				"is_default": 1,
			},
			"name",
		)
		if template:
			return template

		template = frappe.db.get_value(
			"Sales Taxes and Charges Template",
			{
				"company": company,
				"disabled": 0,
				"is_inter_state": 0,
			},
			"name",
		)
		if template:
			return template

	return None


def _apply_item_tax_templates(si) -> list[dict[str, Any]]:
	applied = []

	for row in si.items:
		if not row.item_code:
			applied.append(
				{
					"item_code": None,
					"item_tax_template": None,
					"status": "skipped_no_item_code",
				}
			)
			continue

		item_tax_template = frappe.db.get_value("Item", row.item_code, "item_tax_template")
		if item_tax_template:
			row.item_tax_template = item_tax_template
			applied.append(
				{
					"item_code": row.item_code,
					"item_tax_template": item_tax_template,
					"status": "applied",
				}
			)
		else:
			applied.append(
				{
					"item_code": row.item_code,
					"item_tax_template": None,
					"status": "missing",
				}
			)

	return applied


def _get_shipping_package(so_data, shipping_package_code):
	for pkg in (so_data or {}).get("shippingPackages", []):
		if pkg.get("code") == shipping_package_code:
			return pkg
	return {}


def _get_line_items(
	uni_line_items,
	warehouse,
	so_name,
	cost_center=None,
	warehouse_allocations=None,
):
	from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
	from ecommerce_integrations.unicommerce.constants import MODULE_NAME, ORDER_ITEM_BATCH_NO
	from ecommerce_integrations.unicommerce.order import ORDER_ITEM_CODE_FIELD

	warehouse_allocations = warehouse_allocations or []

	items = []
	for line in uni_line_items:
		item_code = ecommerce_item.get_erpnext_item_code(
			integration=MODULE_NAME,
			integration_item_code=line["itemSku"],
		)

		item_row = {
			"item_code": item_code,
			"qty": 1,
			"rate": line.get("sellingPrice") or line.get("total") or 0,
			"warehouse": warehouse,
			"sales_order": so_name,
			"cost_center": cost_center,
			ORDER_ITEM_CODE_FIELD: line.get("code"),
			ORDER_ITEM_BATCH_NO: None,
		}
		items.append(item_row)

	return items


def _verify_total(si, si_data):
	expected_total = flt(si_data.get("total"))
	if not expected_total:
		return

	if abs(flt(si.grand_total) - expected_total) > 0.5:
		si.add_comment(
			"Comment",
			text=_(
				"Grand Total mismatch with Unicommerce. ERPNext: {0}, Unicommerce: {1}"
			).format(si.grand_total, expected_total),
		)


def attach_unicommerce_docs(
	sales_invoice,
	invoice=None,
	label=None,
	invoice_code=None,
	package_code=None,
):
	def _attach_file(content_b64: str, file_name: str):
		if not content_b64:
			return

		try:
			content = base64.b64decode(content_b64)
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": file_name,
					"attached_to_doctype": "Sales Invoice",
					"attached_to_name": sales_invoice,
					"content": content,
					"is_private": 1,
				}
			).save(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Failed attaching file {file_name} to Sales Invoice {sales_invoice}",
			)


	if invoice:
		_attach_file(invoice, f"{invoice_code or sales_invoice}-invoice.pdf")
	if label:
		_attach_file(label, f"{package_code or sales_invoice}-label.pdf")


def make_payment_entry(si, channel_config, posting_date):
	try:
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", si.name)
		pe.posting_date = posting_date
		if getattr(channel_config, "payment_mode", None):
			pe.mode_of_payment = channel_config.payment_mode
		pe.insert(ignore_permissions=True)
		pe.submit()
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Failed creating Payment Entry for Sales Invoice {si.name}",
		)


def update_cancellation_status(so_data, so):
	status = (so_data or {}).get("status")
	if status == "CANCELLED" and so.docstatus == 1:
		return True
	return False


def create_sales_invoice(
	si_data,
	so_code,
	update_stock=0,
	submit=True,
	shipping_label=None,
	warehouse_allocations=None,
	invoice_response=None,
	so_data=None,
):
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	if not invoice_response:
		invoice_response = {}
	if not so_data:
		so_data = {}

	request_data = {
		"so_code": so_code,
		"invoice_code": si_data.get("code"),
		"shipping_package_code": si_data.get("shippingPackageCode"),
	}

	_log_info(
		f"Starting Sales Invoice creation for SO {so_code}, invoice {si_data.get('code')}",
		request_data=request_data,
	)

	try:
		so = frappe.get_doc("Sales Order", so_code)

		if so_data:
			fully_cancelled = update_cancellation_status(so_data, so)
			if fully_cancelled:
				_log_info(
					f"Sales order {so.name} cancelled before invoicing, skipping invoice creation",
					request_data=request_data,
				)
				return

		channel = so.get(CHANNEL_ID_FIELD)
		facility_code = so.get(FACILITY_CODE_FIELD)

		existing_si = frappe.db.get_value("Sales Invoice", {INVOICE_CODE_FIELD: si_data["code"]})
		if existing_si:
			_log_info(
				f"Sales Invoice {existing_si} already exists for invoice code {si_data['code']}, skipping",
				request_data=request_data,
			)
			return frappe.get_doc("Sales Invoice", existing_si)

		settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
		channel_config = frappe.get_cached_doc("Unicommerce Channel", channel)

		uni_line_items = si_data.get("invoiceItems") or []
		warehouse = settings.get_integration_to_erpnext_wh_mapping(all_wh=True).get(facility_code)

		shipping_package_code = si_data.get("shippingPackageCode")
		shipping_package_info = _get_shipping_package(so_data, shipping_package_code) or {}

		tracking_no = invoice_response.get("trackingNumber") or shipping_package_info.get("trackingNumber")
		shipping_provider_code = (
			invoice_response.get("shippingProviderCode")
			or shipping_package_info.get("shippingProvider")
			or shipping_package_info.get("shippingCourier")
		)
		shipping_package_status = shipping_package_info.get("status")

		si = make_sales_invoice(so.name)

		si_line_items = _get_line_items(
			uni_line_items,
			warehouse,
			so.name,
			getattr(channel_config, "cost_center", None),
			warehouse_allocations,
		)
		si.set("items", si_line_items)

		item_tax_template_result = _apply_item_tax_templates(si)
		_log_info(
			f"Applied item tax templates for invoice {si_data.get('code')}",
			request_data={"item_tax_templates": item_tax_template_result, **request_data},
		)

		tax_template = _get_gst_tax_template(si)
		if not tax_template:
			message = (
				f"Could not determine Sales Taxes and Charges Template for company {si.company} "
				f"while creating invoice for SO {so.name}."
			)
			_log_failure(message, request_data=request_data)
			return

		si.taxes_and_charges = tax_template
		si.set("taxes", [])
		si.set_taxes()

		si.set(INVOICE_CODE_FIELD, si_data["code"])
		si.set(SHIPPING_PACKAGE_CODE_FIELD, shipping_package_code)
		si.set(SHIPPING_PROVIDER_CODE, shipping_provider_code)
		si.set(TRACKING_CODE_FIELD, tracking_no)
		si.set(IS_COD_CHECKBOX, so_data.get("cod"))
		si.set(SHIPPING_METHOD_FIELD, shipping_package_info.get("shippingMethod"))
		si.set(SHIPPING_PACKAGE_STATUS_FIELD, shipping_package_status)
		si.set(CHANNEL_ID_FIELD, channel)
		si.set_posting_time = 1
		si.posting_date = get_unicommerce_date(si_data["created"])
		si.transaction_date = si.posting_date
		si.naming_series = channel_config.sales_invoice_series or settings.sales_invoice_series
		si.delivery_date = so.delivery_date
		si.ignore_pricing_rule = 1
		si.update_stock = False if settings.delivery_note else update_stock
		si.flags.raw_data = si_data

		si.insert()

		_verify_total(si, si_data)

		attach_unicommerce_docs(
			sales_invoice=si.name,
			invoice=si_data.get("encodedInvoice"),
			label=shipping_label,
			invoice_code=si_data.get("code"),
			package_code=shipping_package_code,
		)

		if submit:
			si.submit()
			_log_info(
				f"Submitted Sales Invoice {si.name} for invoice {si_data.get('code')}",
				request_data={**request_data, "sales_invoice": si.name},
			)

		if cint(getattr(channel_config, "auto_payment_entry", 0)):
			make_payment_entry(si, channel_config, si.posting_date)

		_log_success(
			f"Successfully created Sales Invoice {si.name} for SO {so.name}, invoice {si_data.get('code')}",
			request_data={**request_data, "sales_invoice": si.name},
		)
		return si

	except Exception as e:
		_log_failure(
			f"Failed creating Sales Invoice for SO {so_code}, invoice {si_data.get('code')}",
			request_data=request_data,
			exception=e,
		)
		raise


def on_submit(doc, method=None):
	_log_info(
		f"Sales Invoice submit hook executed for {doc.name}",
		request_data={"sales_invoice": doc.name, "doctype": doc.doctype},
	)
	return


def on_cancel(doc, method=None):
	_log_info(
		f"Sales Invoice cancel hook executed for {doc.name}",
		request_data={"sales_invoice": doc.name, "doctype": doc.doctype},
	)
	return


from ecommerce_integrations.unicommerce.utils import create_unicommerce_log, get_unicommerce_date
