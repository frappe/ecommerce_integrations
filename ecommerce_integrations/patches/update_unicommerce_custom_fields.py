import frappe

from ecommerce_integrations.unicommerce.constants import SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.doctype.unicommerce_settings.unicommerce_settings import (
	setup_custom_fields,
)


def execute():
	frappe.reload_doc("unicommerce", "doctype", "unicommerce_settings")

	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	if settings.is_enabled():
		setup_custom_fields()
