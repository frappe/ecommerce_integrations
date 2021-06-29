import frappe

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.unicommerce.constants import MODULE_NAME


def create_unicommerce_log(**kwargs):
	return create_log(module_def=MODULE_NAME, **kwargs)


@frappe.whitelist()
def get_unicommerce_order_url(code: str) -> str:
	if not isinstance(code, str):
		frappe.throw(frappe._("Invalid Order code"))

	site = frappe.db.get_single_value("Unicommerce Settings", "unicommerce_site", cache=True)

	return f"https://{site}/order/orderitems?orderCode={code}"
