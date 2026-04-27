import frappe

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import ORDER_CODE_FIELD, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log


@frappe.whitelist()
def prepare_delivery_note():

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.delivery_note:
		return

	client = UnicommerceAPIClient()

	days_to_sync = min(settings.get("order_status_days") or 2, 14)
	minutes = days_to_sync * 24 * 60

	# find all Facilities
	enabled_facilities = list(settings.get_integration_to_erpnext_wh_mapping().keys())
	enabled_channels = frappe.db.get_list(
		"Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id"
	)

	for facility in enabled_facilities:
		updated_packages = client.search_shipping_packages(updated_since=minutes, facility_code=facility)
		valid_packages = [p for p in updated_packages if p.get("channel") in enabled_channels]
		if not valid_packages:
			continue
		shipped_packages = [p for p in valid_packages if p["status"] in ["DISPATCHED"]]
		for order in shipped_packages:
			if not frappe.db.exists(
				"Delivery Note", {"unicommerce_shipment_id": order["code"]}, "name"
			) and frappe.db.exists("Sales Order", {ORDER_CODE_FIELD: order["saleOrderCode"]}):
				sales_order = frappe.get_doc("Sales Order", {ORDER_CODE_FIELD: order["saleOrderCode"]})
				if frappe.db.exists(
					"Sales Invoice", {"unicommerce_order_code": sales_order.unicommerce_order_code}
				):
					sales_invoice = frappe.get_doc(
						"Sales Invoice", {"unicommerce_order_code": sales_order.unicommerce_order_code}
					)
					try:
						create_delivery_note(sales_order, sales_invoice)
					except Exception as e:
						create_unicommerce_log(status="Error", exception=e, rollback=True)



def create_delivery_note(so, sales_invoice):
    # Create the delivery note
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    from collections import defaultdict

    res = make_delivery_note(source_name=so.name)
    res.unicommerce_order_code = sales_invoice.unicommerce_order_code
    res.unicommerce_shipment_id = sales_invoice.unicommerce_shipping_package_code

    si_item_map = defaultdict(list)
    for si_item in sales_invoice.items:
        si_item_map[si_item.item_code].append(si_item)

    used_si_items = set()

    for item in res.items:
        item.against_sales_invoice = sales_invoice.name
        for si_item in si_item_map.get(item.item_code, []):
            if si_item.name not in used_si_items:
                item.si_detail = si_item.name
                used_si_items.add(si_item.name)
                break

    res.save()
    res.submit()

    log = create_unicommerce_log(method="create_delivery_note", make_new=True)
    frappe.flags.request_id = log.name
    create_unicommerce_log(status="Success")
    frappe.flags.request_id = None

    return res