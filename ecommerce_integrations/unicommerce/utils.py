import frappe

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.unicommerce.constants import MODULE_NAME

SYNC_METHODS = {
	"Items": "ecommerce_integrations.unicommerce.product.upload_new_items",
	"Orders": "ecommerce_integrations.unicommerce.order.sync_new_orders",
	"Inventory": "ecommerce_integrations.unicommerce.inventory.update_inventory_on_unicommerce",
}


def create_unicommerce_log(**kwargs):
	return create_log(module_def=MODULE_NAME, **kwargs)


@frappe.whitelist()
def get_unicommerce_order_url(code: str) -> str:
	if not isinstance(code, str):
		frappe.throw(frappe._("Invalid Order code"))

	site = frappe.db.get_single_value("Unicommerce Settings", "unicommerce_site", cache=True)

	return f"https://{site}/order/orderitems?orderCode={code}"


@frappe.whitelist()
def force_sync(document) -> None:
	frappe.only_for("System Manager")

	method = SYNC_METHODS.get(document)
	if not method:
		frappe.thorow(frappe._("Unknown method"))
	frappe.enqueue(method, queue="long", is_async=True, **{"force": True})