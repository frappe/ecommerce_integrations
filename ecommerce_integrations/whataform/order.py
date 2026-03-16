import json
from typing import Literal, Optional

import frappe
from erpnext.controllers.accounts_controller import get_taxes_and_charges
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate

from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category
from ecommerce_integrations.whataform.constants import (
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.whataform.customer import WhataformCustomer
from ecommerce_integrations.whataform.product import get_item_code
from ecommerce_integrations.whataform.utils import create_whataform_log


def process_message(payload, request_id=None):
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	if frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: cstr(payload["message"])}):
		create_whataform_log(status="Invalid", message="Sales order already exists, not synced")
		return
	try:
		setting = frappe.get_doc(SETTING_DOCTYPE)
		customer = WhataformCustomer(
			# customer_id=payload.get("customer_id"),
			email_id=payload.get(setting.email_field_key),
			mobile_no=payload.get(setting.whatsapp_field_key),
		)
		if not customer.is_matched():
			customer.create_customer(payload)
		else:
			customer.update_existing_addresses(payload)

		create_order(payload, customer, setting)
	except Exception as e:
		create_whataform_log(status="Error", exception=e, rollback=True)
	else:
		create_whataform_log(status="Success")


def create_order(payload, customer, setting, company=None):
	so = create_sales_order(payload, customer, setting, company)
	return so


def create_sales_order(payload, customer, setting, company=None):

	detail_order = payload.get("detail_order")
	items = get_order_items(detail_order, setting)

	tracking = {}
	tracking["source"] = payload.get("utm_source")
	tracking["campaign"] = payload.get("utm_campaign") or payload.get("discount_code")
	# if not (tracking.get("source") and tracking.get("campaign")):
	# 	err = UnderspecifiedTracking(tracking=tracking)
	# 	err.add_note(
	# 		"You may need to add mandatory fields 'utm_source' and 'utm_campaign' to capture tracking info"
	# 	)
	# 	err.add_note(
	# 		"Alternatively, 'utm_source' needs to be set and the promo code needs to correspond to"
	# 		" a campaign name"
	# 	)
	# 	raise err

	# tax master; will be recalculated
	taxes = get_taxes_and_charges("Sales Taxes and Charges Template", setting.tax_master_template)
	so = frappe.get_doc(
		dict(
			{
				"doctype": "Sales Order",
				"naming_series": setting.sales_order_series or "SO-Whataform-",
				ORDER_ID_FIELD: str(payload.get("message")),
				ORDER_NUMBER_FIELD: payload.get("nro"),
				"customer": customer,
				"transaction_date": getdate(payload.get("send")) or nowdate(),
				"delivery_date": getdate(payload.get("send")) or nowdate(),
				"set_warehouse": setting.warehouse,
				"company": setting.company,
				"selling_price_list": get_dummy_price_list(),
				"ignore_pricing_rule": 1,
				"items": items,
				"taxes": taxes,
				"tax_category": get_dummy_tax_category(),
			},
			**tracking
		)
	)
	shipment = get_shipment_value(payload)
	if shipment:
		shipping_rule_doc = frappe.get_doc("Shipping Rule", setting.shipping_rule)
		shipping_rule_doc.add_shipping_rule_to_tax_table(so, shipment)

	if company:
		so.update({"company": company, "status": "Draft"})
	so.flags.ignore_mandatory = True
	so.flags.whataform_message_json = json.dumps(payload)
	so.save(ignore_permissions=True)
	so.submit()

	return so


def get_shipment_value(payload):
	return (
		float(payload.get("total", 0.0))
		- float(payload.get("subtotal", 0.0))
		+ float(payload.get("discount", 0.0))
	)


def get_order_items(order_items, setting):
	items = []
	for _nr, whataform_item in order_items.items():
		item_code = get_item_code(whataform_item)
		# item = frappe.get_doc("Item", item_code)
		items.append(
			{
				"item_code": item_code,
				"item_name": whataform_item.get("product"),
				"rate": whataform_item.get("pricing"),
				"qty": whataform_item.get("cant"),
				# ORDER_ITEM_DISCOUNT_FIELD: (
				# 	_get_total_discount(whataform_item) / cint(whataform_item.get("quantity"))
				# ),
			}
		)
	return items


# def _get_total_discount(line_item) -> float:
# 	discount_allocations = line_item.get("discount_allocations") or []
# 	return sum(flt(discount.get("amount")) for discount in discount_allocations)
