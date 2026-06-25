import frappe

from ecommerce_integrations.medusa.constants import SETTING_DOCTYPE
from ecommerce_integrations.medusa.doctype.medusa_setting.medusa_setting import (
	setup_custom_fields,
)


def execute():
	frappe.reload_doc("medusa", "doctype", "medusa_setting")

	settings = frappe.get_doc(SETTING_DOCTYPE)
	if settings.is_enabled():
		setup_custom_fields()
